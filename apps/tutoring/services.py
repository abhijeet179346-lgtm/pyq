import hashlib
import json
import os
import urllib.request
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from datetime import timedelta

from apps.content.models import Question, QuestionConceptTag
from apps.materials.models import MaterialSection
from apps.tenancy.features import is_enabled
from .models import AIUsage, ConceptMastery, MistakeDiagnosis, SpacedReview, TenantAISettings, TutorAnswerCache


NO_MATERIAL = "I can’t find a supporting section in this institute’s materials yet. I’ve left this for your teacher to review."


def retrieve_sections(tenant, topic, text, concept_ids=(), limit=5):
    """Tenant material first, then global material. Keyword retrieval is the safe fallback."""
    base = MaterialSection.objects.filter(active=True, document__active=True, topic=topic).select_related("document", "topic")
    tenant_rows = base.filter(document__tenant=tenant)
    global_rows = base.filter(document__tenant__isnull=True)
    if concept_ids:
        tenant_rows = tenant_rows.filter(Q(concept_tags__id__in=concept_ids) | Q(concept_tags__isnull=True))
        global_rows = global_rows.filter(Q(concept_tags__id__in=concept_ids) | Q(concept_tags__isnull=True))
    words = [word.strip(".,?!:;()[]{}\"'").lower() for word in text.split() if len(word.strip(".,?!:;()[]{}\"'").lower()) > 3]
    words = list(dict.fromkeys(words))[:12]
    def rank(rows):
        candidates = list(rows.distinct()[:100])
        def score(section):
            body = section.body.lower()
            return sum(body.count(word) for word in words)
        return sorted(candidates, key=score, reverse=True)
    results = rank(tenant_rows)[:limit]
    if len(results) < limit:
        results += rank(global_rows.exclude(pk__in=[row.pk for row in results]))[:limit - len(results)]
    # Optional Pinecone adapter can replace this lexical ordering when embeddings exist.
    if is_enabled(tenant, "semantic_retrieval"):
        results = pinecone_rerank(tenant, topic, text, results, limit) or results
    return results


def pinecone_rerank(tenant, topic, text, fallback, limit):
    """Best-effort Pinecone query. Missing credentials/config always falls back to tenant-safe DB retrieval."""
    host = os.environ.get("PINECONE_INDEX_HOST", "").rstrip("/")
    pinecone_key = os.environ.get("PINECONE_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not (host and pinecone_key and openai_key):
        return None
    try:
        settings = TenantAISettings.objects.filter(tenant=tenant).first()
        if settings is None or settings.monthly_budget_usd <= 0:
            return None
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = AIUsage.objects.filter(tenant=tenant, created_at__gte=month_start).aggregate(value=Sum("cost_usd"))["value"] or Decimal("0")
        if spent >= settings.monthly_budget_usd:
            return None
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        embedding_model = os.environ.get("PYQ_EMBEDDING_MODEL", "text-embedding-3-small")
        req = urllib.request.Request(base + "/embeddings", data=json.dumps({"model": embedding_model, "input": text}).encode(),
            headers={"Authorization": "Bearer " + openai_key, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            embedding_response = json.loads(response.read())
        vector = embedding_response["data"][0]["embedding"]
        usage = embedding_response.get("usage", {})
        record_ai_usage(tenant=tenant, user=None, operation="semantic_query", model=embedding_model,
            input_tokens=int(usage.get("prompt_tokens", 0)))
        matches = []
        for namespace in ("tenant-" + str(tenant.pk), "global"):
            req = urllib.request.Request(host + "/query", data=json.dumps({"vector": vector, "topK": limit,
                "namespace": namespace, "includeMetadata": True}).encode(), headers={
                "Api-Key": pinecone_key, "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=30) as response:
                matches.extend(json.loads(response.read()).get("matches", []))
        ranked_ids = [int(match["id"]) for match in matches if str(match.get("id", "")).isdigit()]
        from apps.materials.models import MaterialSection
        allowed = MaterialSection.objects.filter(pk__in=ranked_ids, topic=topic, active=True, document__active=True).filter(
            Q(document__tenant=tenant) | Q(document__tenant__isnull=True)).select_related("document")
        order = {section_id: index for index, section_id in enumerate(ranked_ids)}
        semantic = sorted(allowed, key=lambda section: order.get(section.pk, limit + 1))
        return semantic[:limit] if semantic else fallback
    except Exception:
        return None


def _call_model(tenant, user, operation, system, prompt, cache_key=""):
    settings = TenantAISettings.objects.filter(tenant=tenant).first()
    if settings is None or settings.monthly_budget_usd <= 0:
        return None
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = AIUsage.objects.filter(tenant=tenant, created_at__gte=month_start).aggregate(value=Sum("cost_usd"))["value"] or Decimal("0")
    if spent >= settings.monthly_budget_usd:
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = settings.model or os.environ.get("PYQ_TUTOR_MODEL", "gpt-4o-mini")
    payload = {"model": model, "temperature": 0.2, "max_tokens": 1200, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.loads(response.read())
        answer = json.loads(result["choices"][0]["message"]["content"])
        usage = result.get("usage", {})
        input_tokens, output_tokens = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        record_ai_usage(tenant=tenant, user=user, operation=operation, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens, cache_key=cache_key)
        return answer
    except Exception:
        return None


def record_ai_usage(*, tenant, user, operation, model, input_tokens=0, output_tokens=0, cache_key=""):
    input_rate = Decimal(os.environ.get("PYQ_AI_INPUT_USD_PER_1K", "0.00015"))
    output_rate = Decimal(os.environ.get("PYQ_AI_OUTPUT_USD_PER_1K", "0.0006"))
    cost = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / Decimal(1000)
    AIUsage.objects.create(tenant=tenant, user=user, operation=operation, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = AIUsage.objects.filter(tenant=tenant, created_at__gte=month_start).aggregate(value=Sum("cost_usd"))["value"] or Decimal("0")
    TenantAISettings.objects.filter(tenant=tenant).update(monthly_spent_usd=spent)
    return cost


def _citations(sections):
    return [{"section_id": section.pk, "title": section.title, "document": section.document.title,
             "page": section.page_reference, "source": section.document.source} for section in sections]


def _update_mastery(tenant, student, tags, correct):
    now = timezone.now()
    evidence = Decimal("85") if correct else Decimal("20")
    for tag in tags:
        mastery, _ = ConceptMastery.objects.get_or_create(tenant=tenant, student=student, concept_tag=tag)
        prior_n = mastery.evidence_count
        mastery.score = evidence if prior_n == 0 else (mastery.score * Decimal("0.8") + evidence * Decimal("0.2"))
        mastery.evidence_count = prior_n + 1
        mastery.last_evidence_at = now
        mastery.next_review_at = now + timedelta(days=7 if correct else 2)
        mastery.save(update_fields=["score", "evidence_count", "last_evidence_at", "next_review_at", "updated_at"])


@transaction.atomic
def record_question_outcome(*, tenant, student, question, correct, practice_attempt=None, test_answer=None):
    links = QuestionConceptTag.objects.filter(question=question).select_related("concept_tag")
    tags = [link.concept_tag for link in links]
    if not tags and question.topic_id:
        from apps.content.models import ConceptTag
        tags = list(ConceptTag.objects.filter(topic=question.topic)[:10])
    _update_mastery(tenant, student, tags, correct)
    if correct:
        SpacedReview.objects.filter(tenant=tenant, student=student, question=question,
            completed_at__isnull=True).update(completed_at=timezone.now())
    elif practice_attempt:
        SpacedReview.objects.get_or_create(practice_attempt=practice_attempt, defaults={"tenant": tenant,
            "student": student, "question": question, "due_at": timezone.now() + timedelta(days=2)})
    elif test_answer:
        SpacedReview.objects.get_or_create(test_answer=test_answer, defaults={"tenant": tenant,
            "student": student, "question": question, "due_at": timezone.now() + timedelta(days=2)})


@transaction.atomic
def tutor_mistake(*, tenant, student, question, selected_answer, practice_attempt=None, test_answer=None):
    if practice_attempt:
        diagnosis, created = MistakeDiagnosis.objects.get_or_create(tenant=tenant, student=student, practice_attempt=practice_attempt)
    else:
        diagnosis, created = MistakeDiagnosis.objects.get_or_create(tenant=tenant, student=student, test_answer=test_answer)
    if not created and diagnosis.explanation:
        return diagnosis
    linked = list(QuestionConceptTag.objects.filter(question=question).select_related("concept_tag"))
    tags = [row.concept_tag for row in linked]
    if not tags and question.topic_id:
        from apps.content.models import ConceptTag
        tags = list(ConceptTag.objects.filter(topic=question.topic)[:10])
    answer_norm = " ".join(str(selected_answer).lower().split())[:300]
    cache_key = hashlib.sha256(f"{question.pk}:{question.version}:{answer_norm}".encode()).hexdigest()
    cached = TutorAnswerCache.objects.filter(tenant=tenant, cache_key=cache_key).first()
    concept_ids = [tag.pk for tag in tags]
    sections = retrieve_sections(tenant, question.topic, question.stem + " " + question.solution,
                                concept_ids=concept_ids) if question.topic_id else []
    citations = _citations(sections)
    direct = next((row for row in linked if row.distractor_reason and row.concept_tag in tags), None)
    explanation, diagnosis_text = "", ""
    chosen_tags = tags
    if direct:
        diagnosis_text = direct.distractor_reason
    if cached:
        explanation, citations = cached.answer, cached.citations
    elif not sections:
        explanation = NO_MATERIAL
    else:
        snippets = [{"id": item.pk, "title": item.title, "source": item.document.title, "page": item.page_reference,
                     "text": item.body[:6000]} for item in sections]
        allowed = [{"id": tag.pk, "name": tag.name} for tag in tags]
        response = _call_model(tenant, student, "mistake_tutor",
            "You are an exam tutor. Use only supplied material. Do not use external knowledge. Return JSON. Cite only material IDs supplied. Choose a concept tag ID only from the allowed tags, otherwise null.",
            json.dumps({"question": question.stem, "student_answer": selected_answer, "correct_answer": question.answer,
                "stored_solution": question.solution, "known_distractor_reason": diagnosis_text,
                "allowed_concepts": allowed, "material": snippets,
                "required": {"concept_id": "integer or null", "diagnosis": "short string", "explanation": "grounded short string", "citation_ids": "array of supplied IDs"}}), cache_key)
        if response:
            diagnosis_text = str(response.get("diagnosis") or diagnosis_text)[:2000]
            explanation = str(response.get("explanation") or "")[:5000]
            selected_tag = next((tag for tag in tags if str(tag.pk) == str(response.get("concept_id"))), None)
            chosen_tags = [selected_tag] if selected_tag else tags
            allowed_citations = {str(section.pk): cite for section, cite in zip(sections, citations)}
            citations = [allowed_citations[value] for value in map(str, response.get("citation_ids", [])) if value in allowed_citations]
            if not citations:
                citations = _citations(sections[:1])
        else:
            explanation = "Review the cited material and compare the definition or formula with the question. A tutor model is not configured or its budget is exhausted."
        if explanation and explanation != NO_MATERIAL:
            TutorAnswerCache.objects.get_or_create(tenant=tenant, cache_key=cache_key,
                defaults={"answer": explanation, "citations": citations})
    easier_qs = Question.objects.filter(topic=question.topic, tenant=question.tenant, status=Question.Status.PUBLISHED,
        difficulty__lt=question.difficulty).exclude(pk=question.pk) if question.topic_id else Question.objects.none()
    if chosen_tags:
        easier_qs = easier_qs.filter(concept_links__concept_tag__in=chosen_tags)
    easier = easier_qs.distinct().order_by("difficulty").first()
    diagnosis.diagnosis = diagnosis_text or "Review the linked concept and compare your answer with the stored solution."
    diagnosis.explanation = explanation
    diagnosis.citations = citations
    diagnosis.rebuild_question = easier
    diagnosis.save(update_fields=["diagnosis", "explanation", "citations", "rebuild_question"])
    diagnosis.concept_tags.set(chosen_tags)
    return diagnosis


def tutor_chat(*, tenant, student, thread, prompt):
    if not thread.topic_id:
        return NO_MATERIAL, []
    history = list(thread.messages.order_by("-created_at").values_list("role", "content")[:8])
    history.reverse()
    cache_key = hashlib.sha256(f"chat:{thread.topic_id}:{json.dumps(history, ensure_ascii=False)}".encode()).hexdigest()
    cached = TutorAnswerCache.objects.filter(tenant=tenant, cache_key=cache_key).first()
    if cached:
        return cached.answer, cached.citations
    retrieval_text = " ".join([text for _, text in history] + [prompt])
    sections = retrieve_sections(tenant, thread.topic, retrieval_text)
    if not sections:
        return NO_MATERIAL, []
    citations = _citations(sections)
    context = [{"id": section.pk, "title": section.title, "source": section.document.title,
                "text": section.body[:6000]} for section in sections]
    styles = {"hint": "Give one useful hint first; do not reveal a full solution unless the student asks.",
              "explain": "Give a clear step-by-step explanation.",
              "simpler": "Explain at a simpler level with a brief analogy where helpful."}
    languages = {"en": "Answer in English.", "hi": "उत्तर हिंदी में दें।", "hinglish": "Answer in Hinglish (Hindi with common English exam terms)."}
    system = "Answer only from the supplied material. If it does not support an answer, say the material does not cover it. Keep within the selected syllabus topic. " + styles.get(thread.teaching_style, styles["hint"]) + " " + languages.get(thread.language, languages["en"]) + " Return JSON with answer and citation_ids."
    response = _call_model(tenant, student, "tutor_chat", system,
        json.dumps({"topic": thread.topic.name, "conversation": [{"role": role, "text": text[:2500]} for role, text in history],
                    "student_question": prompt, "material": context,
                    "required": {"answer": "string", "citation_ids": "array of supplied IDs"}}))
    if not response:
        return "Tutor model unavailable or this institute's monthly AI budget is exhausted. Ask your teacher for help.", citations[:1]
    valid = {str(item.pk): cite for item, cite in zip(sections, citations)}
    cited = [valid[value] for value in map(str, response.get("citation_ids", [])) if value in valid]
    answer = str(response.get("answer") or NO_MATERIAL)[:5000]
    cited = cited or citations[:1]
    TutorAnswerCache.objects.get_or_create(tenant=tenant, cache_key=cache_key,
        defaults={"answer": answer, "citations": cited})
    return answer, cited


@transaction.atomic
def generate_study_questions(*, tenant, user, topic, material_sections):
    """Produce teacher-review drafts from retrieved text; generation never publishes content."""
    tags = list(topic.concept_tags.all()[:30])
    material = [{"id": section.pk, "title": section.title, "text": section.body[:7000]} for section in material_sections[:4]]
    response = _call_model(tenant, user, "question_generation",
        "Create exactly five exam-practice questions using only supplied material. Do not copy questions verbatim. "
        "Return JSON with questions, each containing question_type, stem, options, answer, solution, difficulty, concept_tag_ids. "
        "Use only supported facts and allowed concept IDs. These are drafts for a teacher to verify.",
        json.dumps({"topic": topic.name, "material": material,
            "allowed_concepts": [{"id": tag.pk, "name": tag.name} for tag in tags]}))
    if not response:
        return []
    rows = response.get("questions", [])
    choices = {choice for choice, _ in Question.Type.choices}
    questions, links = [], []
    tag_map = {tag.pk: tag for tag in tags}
    for row in rows[:5]:
        kind = row.get("question_type")
        if kind not in choices: continue
        question = Question(tenant=tenant, kind=Question.Kind.PRACTICE, topic=topic,
            question_type=kind, stem=str(row.get("stem", "")).strip(), options=row.get("options") or [],
            answer=str(row.get("answer", "")).strip(), solution=str(row.get("solution", "")).strip(),
            difficulty=max(1, min(int(row.get("difficulty", 3)), 5)),
            source="AI-generated from institute study material", license="Institute review required",
            status=Question.Status.DRAFT, created_by=user, ai_generated=True)
        if question.duplicate():
            continue
        question.full_clean()
        questions.append(question)
        links.append([tag_map[tag_id] for tag_id in row.get("concept_tag_ids", [])
                      if str(tag_id).isdigit() and int(tag_id) in tag_map])
    from apps.content.models import QuestionConceptTag, without_duplicates
    kept = without_duplicates(questions)
    links = [link for question, link in zip(questions, links) if question in kept]
    created = Question.objects.bulk_create(kept)
    QuestionConceptTag.objects.bulk_create([QuestionConceptTag(question=question, concept_tag=tag)
        for question, question_tags in zip(created, links) for tag in question_tags], ignore_conflicts=True)
    return created
