from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class StaticStorage(ManifestStaticFilesStorage):
    """Fingerprinted static names; a file missing from the manifest falls back to its plain name."""
    manifest_strict = False
