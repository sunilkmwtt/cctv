from django import template

register = template.Library()


@register.filter
def duration(seconds) -> str:
    """3725.4 -> '1h 02m 05s'; 754 -> '12m 34s'."""
    if seconds in (None, ""):
        return "—"
    total = int(round(float(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"
