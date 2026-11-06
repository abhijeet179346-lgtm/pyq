"""Question images ("figures"): validation, storage and URLs.

Images live in private media (never served directly by the web server). Every upload is opened with
Pillow and re-encoded, which drops metadata and anything that is not image data.
"""
import io
import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 6000
MAX_FIGURES = 8
FORMATS = {"PNG", "JPEG", "WEBP", "GIF"}


def clean_image(raw, name="image"):
    """Return (bytes, extension) for a safe re-encoded copy, or raise ValidationError."""
    from PIL import Image, UnidentifiedImageError
    if len(raw) > MAX_BYTES:
        raise ValidationError(f"{name}: images must be 5 MB or smaller.")
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
        image = Image.open(io.BytesIO(raw))
        if image.format not in FORMATS:
            raise ValidationError(f"{name}: use PNG, JPEG, WEBP or GIF.")
        if max(image.size) > MAX_SIDE:
            raise ValidationError(f"{name}: images must be at most {MAX_SIDE}px on each side.")
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValidationError(f"{name}: not a readable image.") from exc
    out = io.BytesIO()
    if image.format == "JPEG":
        image.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
        return out.getvalue(), "jpg"
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        image = image.convert("RGBA")
    image.save(out, "PNG", optimize=True)
    return out.getvalue(), "png"


def store(data, extension, prefix):
    path = f"questions/{prefix}/{uuid.uuid4().hex}.{extension}"
    return default_storage.save(path, ContentFile(data))


def save_upload(upload, prefix):
    data, extension = clean_image(upload.read(), getattr(upload, "name", "image"))
    return store(data, extension, prefix)


def open_figure(path):
    if not isinstance(path, str) or not path or ".." in path or path.startswith("/") or not default_storage.exists(path):
        return None
    return default_storage.open(path, "rb")


def content_type(path):
    return "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
