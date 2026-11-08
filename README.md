# PYQNet — Django Phase 0–3 implementation

Multi-tenant Django pilot for institute-branded PYQ practice, assignments, study materials, teacher review, and a quality-gated AI tutor. SQLite is the default database. Pinecone is optional; retrieval falls back to tenant-scoped lexical search. Each capability is controlled by a per-tenant entitlement and enable switch. AI features also require a passing, teacher-reviewed quality gate.

## Local setup

```sh
cd outputs/pyq_vault_phase0
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py makemigrations accounts tenancy catalog content imports learning assessments features audit materials tutoring scans
python manage.py migrate
python manage.py seed_exam_catalog
python manage.py createsuperuser
python manage.py bootstrap_pilot --tenant-name "Demo Institute" --tenant-slug demo --admin-email admin@example.test
python manage.py runserver 127.0.0.1:8000
```

Bootstrap prompts for the tenant admin password and creates a 60-day pilot trial. Use `localhost:8000/admin/` for the platform superuser, and `demo.localhost:8000/admin/` for the institute admin. If `demo.localhost` does not resolve, add `127.0.0.1 demo.localhost` to the local hosts file.

For provider-backed AI workflows, set `OPENAI_API_KEY`; optionally set `OPENAI_BASE_URL`, `PYQ_TUTOR_MODEL`, and `PYQ_VISION_MODEL`. API keys are deployment secrets, never stored per tenant. Configure an institute’s model names and monthly USD budget in Tenant AI Settings. Provider calls stop when the recorded monthly spend reaches that budget. Usage estimates are token-based and concurrent requests may briefly exceed a budget.

Optional semantic retrieval requires `PINECONE_API_KEY`, `PINECONE_INDEX_HOST`, and an OpenAI-compatible embedding endpoint/model (`PYQ_EMBEDDING_MODEL`, default `text-embedding-3-small`). Create a Pinecone index with the embedding model’s vector dimension, then run `python manage.py index_material_vectors --tenant <id>`. Without Pinecone configuration, the tutor uses tenant-first lexical retrieval. Pinecone namespaces separate tenant and global material records.

## Phase 0 — content foundation

### Exam subjects, question uploads and math

`python manage.py seed_exam_catalog` creates JEE Main, JEE Advanced, NEET, COMEDK, MHT-CET and CUET UG with initial subjects. It preserves later admin changes. A platform superuser can edit an exam's subjects in **Catalog → Exams**. Removing a subject hides its existing PYQs from learner practice; imports and question publishing reject new mismatched exam/subject pairs. Enable an exam for each tenant under **Tenant exams**. The seed command enables COMEDK, MHT-CET and CUET UG for the master tenant. `python manage.py import_new_exam_samples --tenant master` adds two short, sourced, adapted evaluation questions to each new exam; replace these with reviewed licensed papers before broad use.

For a single exam/subject upload, open **Imports → Content jobs → Add**, choose **Excel questions**, select a destination exam and subject, and upload an `.xlsx` file. The workbook may omit its `exam` and `subject` cells; any supplied values must match the selected destination. These rows are global PYQs and need a real `year`, source and rights information. A mixed workbook can leave both selectors blank and supply exam/subject on every row. A `.zip` containing one workbook and named image files attaches diagrams through the `images` column. Admin can preview, remove and reorder each question's images.

To import the supplied PrepWiser chapter-wise JEE Main PDF folders into a tenant's private practice bank, run `python manage.py import_prepwiser_pdfs --root ~/Downloads/PrepWiser-JEE-Main-PYQ-PDFs --tenant demo`. It searches subfolders recursively, takes answers from each PDF's numbered answer key, and gets question text, options and metadata from the PDF's linked question pages. The import is repeatable: existing source links are skipped. Use `--dry-run` to inspect without writing, `--workers 16` to tune fetch concurrency, or `--refresh-figures` to retry/refresh diagrams for existing questions. Step-by-step solutions are not copied.

To copy existing PrepWiser questions and their figures between tenants without fetching them again, use `python manage.py copy_prepwiser_questions --source-tenant demo --target-tenant master`. Existing matching questions are skipped; `--dry-run` checks the counts and source figures first.

Use `$...$` for inline LaTeX and `$$...$$` for display equations in stems, options and solutions. Learner question and test pages render them with the bundled MathJax. `python manage.py repair_question_math` previews conservative repairs to old encoding errors and bare LaTeX tables; `--apply` writes revisions and repaired text. Flattened OCR equations cannot be reconstructed reliably from plain text and need comparison with their source paper.

### Main application

The core domain `http://localhost:8765/` is for direct learners. Enter email and password in the combined form at `/` or `/login/`: an existing email signs in, while a new email automatically creates an account in the master tenant. No separate registration screen is required. After email verification, a direct learner goes straight to the personal learning workspace without an institute or access code. Google identities follow the same flow after Google validates their email.

Direct practice is at `/practice/`, shared published study material at `/library/`, and private practice history at `/progress/`. Direct learners can access enabled global PYQ exams, master-tenant practice questions, and master/global active study sections, subject to the master tenant feature switches. Institute questions, materials, and batches remain tenant-scoped. Direct practice attempts are stored separately from institute practice and cannot be read by another learner. A source-attributed 100-question evaluation bank is available for import with `python manage.py import_pyq_sample --tenant demo`; see `data/README.md` for exam papers, source links, and coverage.

Institute subdomains such as `http://demo.localhost:8765/` use the same learning interface with their own branding. On these subdomains, learners activate a batch with that institute's access code at `/activate/`. The code preserves its role, batch, expiration, revocation, usage cap, and seat limit. Each account belongs to one tenant. A master-tenant learner can use an institute code on its subdomain to create a linked institute account; the original master account and personal history stay intact. Another institute's code cannot move an existing institute account. Core learners are never redirected to institute enrollment automatically.

The shared question bank is at `/practice/` and assigned institute tests are at `/tests/`. Tenant capabilities follow their feature entitlements, and institute practice answers also feed batch reports. Platform/institute management remains in Django admin at `/admin/`. Local development is not publicly hosted.

To enable Google, create a Google OAuth **Web application** client and register `http://localhost:8765/auth/google/callback/` as an authorized redirect URI. Export `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, then restart Django. See `.env.example` for variable names; this project reads environment variables and does not load that file automatically. The implementation uses Google's authorization-code flow with state, nonce, PKCE, and server-side ID-token verification, as described in [Google's OpenID Connect documentation](https://developers.google.com/identity/openid-connect/openid-connect). It requests only identity, email, and profile scopes. Configure `PYQ_CORE_ORIGIN` and `PYQ_TENANT_BASE_DOMAIN` for a hosted domain, and register its HTTPS callback. Google always returns to the core domain, avoiding a separate OAuth callback registration for every tenant.

Google remains disabled in the UI until real client credentials are provided. On institute domains it additionally requires the optional `google_login` entitlement, which is enabled for the local demo and can be switched off per tenant. Provider callbacks are covered by mocked regression tests; a real Google round trip requires your configured OAuth client. Never commit client secrets.
### Email verification and delivery

When `REQUIRE_EMAIL_VERIFICATION=true`, password accounts must verify their email before they can activate an access code, open a workspace, or access staff admin pages. The verification email contains a one-hour, single-use link. Opening it displays confirmation; a signed-in user must submit the confirmation form, so mail scanners cannot consume the link. Tokens are stored as digests, are bound to the account's email and password state, and older links are invalidated on resend. Resends are limited to one per minute and ten per day per account. Changing the account email clears its verification. Google identities are verified only after Google token validation; unverified pre-existing passwords are removed when an authoritative Google identity claims the mailbox.

Verification is disabled by default in local settings and enabled by default in the base settings. Set `REQUIRE_EMAIL_VERIFICATION=true` locally to restore the verification flow; local settings print its messages in the server terminal. For inbox delivery, export `DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `DEFAULT_FROM_EMAIL`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, and `EMAIL_HOST_PASSWORD`, with the TLS/SSL settings required by the provider. See `.env.example`; restart the server after changing deployment environment variables. No SMTP credentials are included in this repository.

Student feedback is saved in the tenant's admin review queue and sends a notification to the comma-separated `FEEDBACK_NOTIFICATION_RECIPIENTS`. Configure `FEEDBACK_FROM_EMAIL`, the SMTP `EMAIL_*` settings, and optionally `FEEDBACK_EMAIL_BACKEND` when notifications need a different backend from other mail. If delivery fails, the feedback stays saved and an administrator can use the **Retry pending email notifications** action in the feedback list. Configure the environment on each deployed server and restart the application.

### Master tenant controls in admin

Core-domain learners belong to the singleton **PYQNet — Master tenant**, identified by `is_master=True`. The core host resolves to this tenant; it is not exposed through a `master` institute subdomain. A migration moves existing non-staff, non-superuser core accounts into it. Newly created learner accounts without an explicit institute are assigned to it automatically. Platform superusers remain outside all tenants.

Open **Tenancy → Tenants → PYQNet — Master tenant** in Django admin. Its edit page includes feature-entitlement and exam-setting rows. The same records can be managed under **Features → Feature entitlements** and **Tenant exams**. Disabling a feature blocks its core URL and removes its navigation/card. Disabled exams hide their PYQs; suspending the master tenant blocks its learner workspace. The master tenant's marker and slug are read-only and admin deletion is blocked.

Core users are listed under **Accounts → Users** with the master tenant selected. To supply core-only questions, create practice questions owned by the master tenant. Global PYQs remain global and are exposed only when their exam is enabled for the master tenant. For core-only study material, select the master tenant as document owner; shared global material can also be shown. Private institute content stays excluded. Core practice history is visible to platform administrators under **Portal → Direct practice attempts**. New exams need an explicit enabled Tenant Exam row; they are not automatically exposed. The migration enables the three already imported sample exams and common learning capabilities once, preserving subsequent admin edits.

### Platform superuser

Platform administration stays at `/admin/`. The main core-domain login is for direct learners; if a superuser uses that form, it redirects to Django admin. The local development superuser remains available with the credentials provided in chat. Its email was preverified for local development; ordinary accounts complete email verification.
Run the access and portal regression suite with `python manage.py test apps.accounts apps.portal`.

Create subjects, chapters, topics, and concept tags in Django admin. Import question CSVs from `/manage/import/`; previews validate every row and imports create drafts for editorial review. CSV options are JSON arrays. Required columns: `question_type,stem,options,answer,solution,difficulty,subject,chapter,topic,source,license`. Question reports route questionable global PYQs to review. The repository does not include a licensed 1,000-question corpus; supply reviewed content separately.

## Phase 1 — multi-tenant pilot

On an institute host, create batches and enable tenant-specific features in Tenant Feature Entitlements. Enable exams under Tenant Exams to expose global PYQs. `/manage/access-codes/` issues capped or single-use enrollment codes; students continue with email/password or Google, then activate the code at `/activate/` and practice from `/learn/`. Create test templates, sections, and questions in admin, then assign tests to a batch. Marking rules are JSON, for example `{"correct":4,"incorrect":-1,"unanswered":0}`. Attempts use server-side deadlines, autosave, section timers, shuffling, and tab-switch event capture. `/learn/` links to teacher batch analytics. Institute branding is available at `/manage/branding/`.

## Phase 2 — tutor, mastery, and teacher analytics

Add active, licensed course material in Materials admin. Upload text PDFs, Markdown, or TXT for extraction with `python manage.py process_material_imports`; scanned documents belong in the scan import workflow. Teachers can curate sections, concept tags, and video links. The student NCERT Studio is at `/study/topics/<topic-id>/`; teacher projector mode is at `/teach/batches/<batch-id>/projector/`.

Tutor chat is at `/tutor/`. Responses cite retrieved material and refuse unsupported questions when no relevant source is found. Students can request photo transcription, retry mistakes with a same-concept draft question, and view due spaced reviews and mastery. Teachers can review class analytics, respond to escalations, and convert useful explanations into material sections.

Before enabling AI chat, collect at least 300 teacher-reviewed golden cases with at least 90% answer approval and citation support. Mistake diagnosis additionally requires 500 cases and at least 85% concept diagnosis accuracy. Add and review cases in Tutor Golden Cases admin, then run `python manage.py evaluate_tutor_golden_set --tenant <id>`; use `--recompute-only` after changing human labels or `--re-evaluate` to call the model again. AI features remain unavailable until the quality and tenant entitlement gates both pass. Set a non-zero monthly budget and provider configuration first.

## Phase 3 — OCR imports and pilot operations

Enable `scan_import` for a tenant, then upload licensed PDFs or images at `/manage/scans/`. Run `python manage.py process_scan_imports --all-pages` in a worker. The worker processes PDFs in resumable batches (up to 1,500 pages / 100 MB), extracts candidate questions and figure crops into a tenant-scoped database review queue, and never publishes automatically. Correct question text, topic, options, printed answers, and suggested tags, then accept selected rows. New questions remain drafts with source-page references; publish only after editorial review. Student pages serve the associated scan page and figure crops through authenticated, tenant-scoped routes.

The default OCR backend uses the configured vision model. Install `pip install -r requirements-ocr.txt` on the worker to enable local Docling layout, reading-order, OCR, and formula transcription. In Django admin, choose **Tenant OCR settings → Backend** per institute: **Docling local OCR and question parsing** works without an API key but uses conservative numbering and answer-marker rules; **Docling layout and OCR + vision model** also uses the page image for better question segmentation and requires `OPENAI_API_KEY` plus a nonzero tenant AI budget. Package/model installation may download weights. Tenant OCR settings also control batch size (1–25 pages). Use `--tenant <slug>` to process one institute, or `--retry-failed --tenant <slug> --all-pages` to resume failed work from the last saved page. Human review is required because layouts, math, answer keys, and cross-page questions can be misread.

Tenant subscriptions have trial, paid, grace, and suspended states. Run `python manage.py sync_tenant_subscriptions` periodically to advance expired accounts. Grace access is read-only and limited to tests/results, analytics, mastery, and result export; suspended tenants are blocked. Payment collection and subscription status updates are manual; no payment gateway is integrated.

## Tenant feature controls

The feature registry lives in `apps/tenancy/features.py`. A feature is active only when implemented, entitled for that tenant, enabled by its override, and all prerequisite features are active. Tenant administrators can manage their enabled switches; only platform administrators grant entitlements. AI switches additionally enforce the golden-set quality thresholds. No tenant receives every feature automatically.

## Operations and boundaries

- Local files are stored under `private-media/`; do not expose this directory through a static web server. Use authenticated views for scan-derived images. For hosted deployments, configure durable private object storage and back up uploaded source materials.
- Background workflows are management commands, not a task queue. Schedule material extraction, scan processing, vector indexing, and subscription synchronization in a controlled worker/cron environment. Idempotency is implemented for accepted scan rows; review failed runs before retrying.
- SQLite is suitable for a local demo and a small pilot with low write concurrency. Move to PostgreSQL and shared cache/object storage before serving concurrent tenants at scale. Access-code throttling uses local-memory cache by default.
- OCR and AI require external provider credentials and incur provider costs. OCR output, generated explanations, and generated questions require human review. Pinecone is optional and stores vectors/metadata, not the source of truth.
- Phase completion gates in the PRD require external evidence: a 1,000-question licensed corpus, a weekly active pilot batch, quality-reviewed evaluation data, and paying institute partners. Code in this repository cannot establish those business/content gates.
- No Django runtime is bundled. Install `requirements.txt` before using management commands. Migration files are generated locally by the setup command above.

## Main modules

- `apps/tenancy`: host tenant resolution, feature gates, branding, subscriptions
- `apps/content`, `apps/catalog`, `apps/imports`: questions, taxonomy, CSV import and review
- `apps/learning`, `apps/assessments`: enrollment, practice, timed tests, results and exports
- `apps/materials`: document extraction, NCERT Studio, video mapping, projector view
- `apps/tutoring`: grounded AI, mistake remediation, mastery, golden-set evaluation
- `apps/scans`: private OCR workflow, figure crops, draft review and source attribution
- `apps/audit`: tenant-scoped audit events

## PYQ data for local evaluation

The current SQLite database includes 18 actual PYQ samples: six each from JEE Main 2020, JEE Advanced 2024, and NEET 2020. These use concise wording and link to the original paper and checked answer reference. See `data/README.md` and `data/pyq_sample.json`. The importer is repeatable and available as `python manage.py import_pyq_sample --tenant demo`.

## Reference interface adaptation

The learner interface has been adapted from the supplied `PYQ-vault-main.zip`: top navigation, an indigo hero, exam cards, study-tool cards, subject/chapter navigation, question answer cards, dark mode and mobile bottom navigation. It uses Django templates and the existing SQLite data and tenant feature controls. See [reference-interface.md](reference-interface.md) for the comparison and remaining modules.

## Topic and subtopic division

Core and institute question banks support exam → subject → chapter → topic → subtopic browsing. Counts and selections are scoped to accessible published questions. The sample bank has all 100 questions classified. Manage Topics and Subtopics in Django admin and assign them in Content → Questions. Apply schema changes with `python manage.py migrate`; refresh an existing sample taxonomy with `python manage.py import_pyq_sample --tenant master --refresh-taxonomy`.

## Warm Slate practice flow

The core learner interface uses warm neutral surfaces and sage accents. Open an exam to see subject cards, then open a subject to select chapters, topics or subtopics. No question list is shown until Browse questions is chosen. Whole-exam and whole-subject actions are available without choosing individual sections. Multiple checked sections are combined; selecting a parent includes its descendants. Year and search filters narrow the selected bank.

Start practice creates a session containing the selected published questions, with one question per page, immediate answer feedback, previous/next navigation, an optional full-question page, and a summary. Answer records remain in the learner's existing practice history. Sessions belong to the signed-in learner and tenant; access is rechecked when each question is opened. The most recent five sessions are kept in the browser's Django session. This is untimed bank practice, rather than a simulated official exam paper.

## Expanded question banks and revision library

There are now **500 questions for each of JEE Main, NEET and JEE Advanced** (1,500 total), with topic/subtopic tags and estimated difficulty. The library includes **51 original revision notes**, with exam/subject/chapter filters and individual reading pages. See [data/EXPANDED_BANKS.md](data/EXPANDED_BANKS.md) for sources, metadata limitations, importer commands and reproducibility. Import `pyq_sample` followed by `exam_banks` to populate a new database; then run `enrich_question_metadata` and `seed_revision_notes`.

## Current palette: Academic Blue

The active learner and login stylesheet is `academic-blue.css`: background `#F6F8FA`, cards `#FFFFFF`, text `#253044`, and dusty blue accent `#46688C`. Navigation, buttons, selected answers, tags, focus rings and dark mode use matching blue tones. Correct/incorrect answer feedback retains semantic colours. The layout and practice flow are unchanged. The earlier Warm Slate stylesheet is retained for reference.

## Timed tests and content administration

Learners can generate full or subject timed tests from an enabled exam's question bank. The platform also supports server-timed attempts with autosave and partial marking, Excel admin imports, large PDF-to-Excel jobs, and study-section recommendations after mistakes. See [TIMED_TESTS_AND_CONTENT_ADMIN.md](TIMED_TESTS_AND_CONTENT_ADMIN.md) for formats, admin steps, limits, source attribution and OCR requirements.
