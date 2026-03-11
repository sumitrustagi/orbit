"""
DID range utilities — E.164 arithmetic for generating number ranges.
"""
import re
from typing import Generator


def generate_e164_range(start: str, end: str) -> list[str]:
    """
    Generate all E.164 numbers between start and end inclusive.
    Both must share the same country code prefix.

    Example:
        generate_e164_range("+3222000100", "+3222000105")
        → ["+3222000100", "+3222000101", ..., "+3222000105"]
    """
    start_clean = re.sub(r"[^\d+]", "", start)
    end_clean   = re.sub(r"[^\d+]", "", end)

    prefix = _extract_prefix(start_clean)
    s_num  = int(start_clean.lstrip("+"))
    e_num  = int(end_clean.lstrip("+"))

    if s_num > e_num:
        raise ValueError(f"Range start ({start}) must be ≤ end ({end}).")

    max_range = 10_000
    if (e_num - s_num + 1) > max_range:
        raise ValueError(
            f"Range too large: {e_num - s_num + 1} numbers "
            f"(maximum {max_range} per pool)."
        )

    return [f"+{n}" for n in range(s_num, e_num + 1)]


def _extract_prefix(e164: str) -> str:
    """Extract country code prefix from E.164 number."""
    stripped = e164.lstrip("+")
    # Common country code lengths: 1 (US/CA), 2, 3
    return "+" + stripped[:3]


def validate_e164(number: str) -> bool:
    """Return True if number is valid E.164 format."""
    return bool(re.match(r"^\+[1-9]\d{6,14}$", number.strip()))


def next_available_number(pool_id: int) -> str | None:
    """
    Atomically reserve and return the next available DID from a pool.
    Uses a SELECT FOR UPDATE to prevent race conditions when multiple
    ServiceNow requests arrive simultaneously.
    """
    from app.extensions import db
    from app.models.did import DIDAssignment, DIDStatus

    try:
        number = (
            db.session.query(DIDAssignment)
            .filter_by(pool_id=pool_id, status=DIDStatus.AVAILABLE)
            .order_by(DIDAssignment.number)
            .with_for_update(skip_locked=True)
            .first()
        )
        if number:
            number.status = DIDStatus.RESERVED   # Mark reserved until fully assigned
            db.session.flush()
            return number.number
        return None
    except Exception as exc:
        db.session.rollback()
        raise exc
