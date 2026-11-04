from django import template

register = template.Library()


@register.filter
def duration(seconds):
    """Seconds as '1h 04m', '4m 05s' or '38s'."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@register.filter
def marks(value):
    """2.0 -> '2', 2.5 -> '2.5', with an explicit plus sign for positive marks."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    text = f"{number:g}"
    return f"+{text}" if number > 0 else text
