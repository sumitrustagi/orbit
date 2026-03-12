"""
Custom Jinja2 filters and template utilities registered in the app factory.
"""
from datetime import datetime, timezone
from typing import Any


def register_filters(app) -> None:

    @app.template_filter("timeago")
    def timeago(dt: datetime | None) -> str:
        """Convert a datetime to a human-readable 'X ago' string."""
        if not dt:
            return "—"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now   = datetime.now(timezone.utc)
        delta = now - dt
        secs  = int(delta.total_seconds())

        if secs < 60:
            return "just now"
        if secs < 3600:
            m = secs // 60
            return f"{m} minute{'s' if m != 1 else ''} ago"
        if secs < 86400:
            h = secs // 3600
            return f"{h} hour{'s' if h != 1 else ''} ago"
        d = secs // 86400
        return f"{d} day{'s' if d != 1 else ''} ago"

    @app.template_filter("fmt_dt")
    def fmt_dt(dt: datetime | None, fmt: str = "%d %b %Y %H:%M") -> str:
        """Format a datetime with a given strftime format."""
        if not dt:
            return "—"
        return dt.strftime(fmt)

    @app.template_filter("pluralise")
    def pluralise(value: int, singular: str, plural: str = "") -> str:
        """Return singular or plural form based on value."""
        if value == 1:
            return f"{value} {singular}"
        return f"{value} {plural or singular + 's'}"

    @app.template_filter("truncate_mid")
    def truncate_mid(value: str, max_len: int = 40) -> str:
        """Truncate a long string in the middle: 'abc…xyz'."""
        if not value or len(value) <= max_len:
            return value
        half = (max_len - 3) // 2
        return value[:half] + "…" + value[-half:]

    @app.template_filter("status_badge")
    def status_badge(status: str) -> str:
        """Return a Bootstrap badge HTML string for a status value."""
        MAP = {
            "fulfilled":  ("success",   "Fulfilled"),
            "pending":    ("warning",   "Pending"),
            "processing": ("primary",   "Processing"),
            "retrying":   ("warning",   "Retrying"),
            "failed":     ("danger",    "Failed"),
            "active":     ("success",   "Active"),
            "paused":     ("secondary", "Paused"),
            "completed":  ("success",   "Completed"),
            "assigned":   ("primary",   "Assigned"),
            "available":  ("success",   "Available"),
            "quarantine": ("warning",   "Quarantine"),
            "reserved":   ("secondary", "Reserved"),
        }
        key  = (status or "").lower()
        col, label = MAP.get(key, ("secondary", status or "—"))
        return (
            f'<span class="badge bg-{col}-subtle text-{col}" '
            f'style="font-size:0.68rem;font-weight:700;">{label}</span>'
        )

    @app.template_filter("yesno")
    def yesno(value: Any, yes: str = "Yes", no: str = "No") -> str:
        return yes if value else no
