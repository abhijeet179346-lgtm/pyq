import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def load_env_file(path):
    # Real environment variables win; blank values are skipped so defaults apply.
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-local-only-change-me")
DEBUG = False
ALLOWED_HOSTS = [host for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,.localhost,127.0.0.1,testserver").split(",") if host]

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "apps.tenancy", "apps.accounts", "apps.catalog",
    "apps.features", "apps.audit", "apps.content", "apps.imports", "apps.learning", "apps.assessments",
    "apps.materials", "apps.tutoring", "apps.scans", "apps.portal",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.tenancy.middleware.TenantResolutionMiddleware",
    "apps.portal.middleware.WorkspaceActivationMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "apps.portal.context_processors.portal_context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
def database_from_url(url):
    # postgres(ql)://user:password@host:port/name?sslmode=require
    from urllib.parse import parse_qsl, unquote, urlsplit

    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parts.scheme}")
    options = dict(parse_qsl(parts.query))
    options.setdefault("sslmode", os.environ.get("DATABASE_SSLMODE", "require"))
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parts.path.lstrip("/")) or "postgres",
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        "CONN_MAX_AGE": int(os.environ.get("DATABASE_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }


SQLITE_DATABASE = {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3", "OPTIONS": {"timeout": 20}}
DATABASES = {"default": database_from_url(os.environ["DATABASE_URL"]) if os.environ.get("DATABASE_URL") else SQLITE_DATABASE}
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.TenantAwareBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = Path(os.environ.get("PYQ_MEDIA_ROOT", BASE_DIR / "private-media"))
MEDIA_URL = "/private-media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
PYQ_TENANT_BASE_DOMAIN = os.environ.get("PYQ_TENANT_BASE_DOMAIN", "localhost")
PYQ_CORE_ORIGIN = os.environ.get("PYQ_CORE_ORIGIN", "http://localhost:8765").rstrip("/")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "PYQNet <noreply@localhost>")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() == "true"
EMAIL_TIMEOUT = 15
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
# Behind Caddy (HTTPS). gunicorn listens only on 127.0.0.1, so the proxy header can be trusted.
if os.environ.get("DJANGO_HTTPS", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
    SECURE_CONTENT_TYPE_NOSNIFF = True
REST_FRAMEWORK = {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]}
# Fingerprinted static file names (app.3f2a9c.css) so each deploy busts browser caches.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "config.storage.StaticStorage"},
}
