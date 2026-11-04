FEATURES = {
    "google_login": "Sign in with Google",
    "global_pyq_bank": "Browse and practice global PYQs",
    "tenant_question_bank": "Institute-owned questions and uploads",
    "study_material": "Study material library",
    "practice": "Untimed practice sessions",
    "tests": "Timed tests and scoring",
    "custom_test_assignment": "Teacher-assigned custom tests",
    "test_integrity": "Question shuffle and tab-switch event capture",
    "ai_tutor_mistake": "Mistake diagnosis and remediation",
    "ai_tutor_chat": "Tutor chat",
    "photo_question_input": "Photo transcription for tutor questions",
    "teacher_escalation": "Route student doubts to teachers",
    "semantic_retrieval": "Vector-backed semantic retrieval",
    "bulk_question_import": "CSV question import",
    "material_import": "Study material import",
    "scan_import": "Scanned PDF question extraction",
    "ai_question_generation": "Generate teacher-reviewable practice questions",
    "mastery_analytics": "Concept mastery analytics",
    "batch_analytics": "Batch dashboards and exports",
    "question_reporting": "Student question issue reports",
    "video_mapping": "Concept video links",
    "ncert_studio": "Study material and questions split view",
    "projector_mode": "Teacher projector view",
    "access_code_enrollment": "Access-code student enrollment",
    "batch_management": "Manage classes and student batch membership",
    "teacher_assignments": "Assign teachers to batches and subjects",
    "branding_settings": "Institute name, color, logo, and welcome settings",
    "import_reports": "View question import batch reports",
    "result_export": "Export student and test results",
    "custom_domain": "Custom tenant domain",
    "advanced_exam_patterns": "Advanced section and optional-question scoring",
}

PLATFORM_KILL_SWITCHES = set()
# A registry entry reserves a tenant switch for a later phase; it is not enabled
# until the corresponding product path has shipped.
IMPLEMENTED_FEATURES = {
    "google_login",
    "global_pyq_bank", "tenant_question_bank", "practice", "tests",
    "custom_test_assignment", "test_integrity", "bulk_question_import", "batch_analytics",
    "access_code_enrollment",
    "batch_management", "teacher_assignments", "branding_settings", "import_reports",
    "study_material",
    "ai_tutor_mistake", "ai_tutor_chat", "teacher_escalation", "semantic_retrieval",
    "material_import", "scan_import", "mastery_analytics", "video_mapping", "ncert_studio",
    "projector_mode", "question_reporting", "result_export",
    "ai_question_generation", "photo_question_input",
}
DEPENDENCIES = {
    "test_integrity": ("tests",),
    "custom_test_assignment": ("tests",),
    "ai_tutor_mistake": ("practice", "study_material"),
    "ai_tutor_chat": ("study_material",),
    "photo_question_input": ("ai_tutor_chat",),
    "teacher_escalation": ("ai_tutor_chat",),
    "semantic_retrieval": ("study_material",),
    "ai_question_generation": ("study_material",),
    "scan_import": ("tenant_question_bank", "bulk_question_import"),
    "mastery_analytics": ("practice",),
    "ncert_studio": ("study_material",),
    "projector_mode": ("study_material", "global_pyq_bank"),
    "advanced_exam_patterns": ("tests",),
    "access_code_enrollment": ("batch_management",),
    "teacher_assignments": ("batch_management",),
    "batch_analytics": ("batch_management",),
    "custom_test_assignment": ("batch_management",),
    "import_reports": ("bulk_question_import",),
}


def is_enabled(tenant, key):
    """Feature evaluation shared by views, admin actions and future task services."""
    return _is_enabled(tenant, key, seen=set())


def _is_enabled(tenant, key, seen):
    if key not in FEATURES or key not in IMPLEMENTED_FEATURES or key in seen or key in PLATFORM_KILL_SWITCHES or tenant is None or tenant.status != "active":
        return False
    subscription = getattr(tenant, "subscription", None)
    if subscription and subscription.status in {"grace", "suspended", "cancelled"}:
        if subscription.status != "grace" or key not in {"tests", "batch_analytics", "mastery_analytics", "result_export"}:
            return False
    setting = tenant.feature_entitlements.filter(key=key).first()
    if setting is None or not setting.entitled:
        return False
    if key in {"ai_tutor_mistake", "ai_tutor_chat"}:
        from apps.tutoring.models import TenantAISettings
        ai = TenantAISettings.objects.filter(tenant=tenant).first()
        if ai is None:
            return False
        if key == "ai_tutor_mistake" and (ai.diagnosis_golden_set_size < 500 or ai.diagnosis_accuracy_percent < 85):
            return False
        if key == "ai_tutor_chat" and (ai.answer_golden_set_size < 300 or ai.answer_accuracy_percent < 90 or ai.citation_support_percent < 90):
            return False
    enabled = setting.enabled_override if setting.enabled_override is not None else True
    if not enabled:
        return False
    return all(_is_enabled(tenant, dependency, seen | {key}) for dependency in DEPENDENCIES.get(key, ()))
