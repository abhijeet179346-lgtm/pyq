from .base import *  # noqa: F401,F403

DEBUG = True
EMAIL_BACKEND = os.environ.get("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
# Plain static names locally; runserver serves files directly without collectstatic.
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
