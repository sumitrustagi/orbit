"""
ServiceNow REST API client.

Handles:
  - Fetching request / RITM details by number or SysID
  - Updating request item state
  - Appending work notes / comments
  - Closing / fulfilling items

All methods return (success, data_or_error_msg).
Credentials are read from AppConfig at call time — no caching —
so token rotation takes effect immediately.
"""
import logging
from typing import Tuple, Any

import requests
from requests.auth import HTTPBasicAuth

from app.utils.crypto import decrypt

logger = logging.getLogger(__name__)

_TIMEOUT = 15   # seconds


def _get_credentials() -> Tuple[str, str, str]:
    """
    Return (instance_url, username, password) from AppConfig.
    Falls back to environment variables if AppConfig is not populated.
    """
    import os
    try:
        from app.models.app_config import AppConfig
        instance = AppConfig.get("SNOW_INSTANCE", "")
        username = AppConfig.get("SNOW_USERNAME", "")
        password = AppConfig.get("SNOW_PASSWORD", "")   # stored encrypted
        if instance and username and password:
            return instance.rstrip("/"), username, decrypt(password)
    except Exception:
        pass

    instance = os.environ.get("SNOW_INSTANCE", "").rstrip("/")
    username = os.environ.get("SNOW_USERNAME", "")
    password = decrypt(os.environ.get("SNOW_PASSWORD", ""))
    return instance, username, password


def _auth() -> HTTPBasicAuth:
    _, u, p = _get_credentials()
    return HTTPBasicAuth(u, p)


def _base() -> str:
    instance, _, _ = _get_credentials()
    return f"{instance}/api/now"


def _headers() -> dict:
    return {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "X-no-response-body": "false",
    }


# ── Fetch ─────────────────────────────────────────────────────────────────────

def get_request_by_number(number: str) -> Tuple[bool, Any]:
    """
    Fetch a sc_request or sc_req_item record by its number (e.g. REQ0012345 / RITM0012345).
    Returns (True, record_dict) or (False, error_str).
    """
    table = "sc_req_item" if number.upper().startswith("RITM") else "sc_request"
    try:
        resp = requests.get(
            f"{_base()}/table/{table}",
            params={
                "sysparm_query":  f"number={number}",
                "sysparm_limit":  1,
                "sysparm_fields": (
                    "sys_id,number,state,short_description,"
                    "requested_for,requested_for.email,"
                    "requested_for.name,cat_item,opened_at,"
                    "variables,assignment_group,assigned_to"
                ),
            },
            auth=_auth(),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        records = resp.json().get("result", [])
        if not records:
            return False, f"No record found for {number}."
        return True, records[0]
    except requests.exceptions.ConnectionError:
        return False, "Cannot reach ServiceNow — check instance URL and connectivity."
    except Exception as exc:
        logger.error(f"[SNOW] get_request_by_number({number}): {exc}")
        return False, str(exc)


def get_request_by_sysid(sys_id: str, table: str = "sc_req_item") -> Tuple[bool, Any]:
    """Fetch a single SNOW record by SysID."""
    try:
        resp = requests.get(
            f"{_base()}/table/{table}/{sys_id}",
            auth=_auth(),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True, resp.json().get("result", {})
    except Exception as exc:
        logger.error(f"[SNOW] get_request_by_sysid({sys_id}): {exc}")
        return False, str(exc)


# ── Update state ──────────────────────────────────────────────────────────────

def update_request_state(
    sys_id:    str,
    state:     int,
    table:     str = "sc_req_item",
) -> Tuple[bool, str]:
    """
    Update the state of a SNOW request item.
    state=3 → Fulfilled, state=4 → Closed Incomplete (failed).
    """
    try:
        resp = requests.patch(
            f"{_base()}/table/{table}/{sys_id}",
            json={"state": str(state)},
            auth=_auth(),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True, f"State updated to {state}."
    except Exception as exc:
        logger.error(f"[SNOW] update_state({sys_id}, {state}): {exc}")
        return False, str(exc)


# ── Work notes / comments ─────────────────────────────────────────────────────

def add_work_note(
    sys_id:    str,
    note:      str,
    table:     str = "sc_req_item",
) -> Tuple[bool, str]:
    """Append an internal work note to a SNOW request item."""
    try:
        resp = requests.patch(
            f"{_base()}/table/{table}/{sys_id}",
            json={"work_notes": note},
            auth=_auth(),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True, "Work note added."
    except Exception as exc:
        logger.error(f"[SNOW] add_work_note({sys_id}): {exc}")
        return False, str(exc)


def add_comment(
    sys_id:    str,
    comment:   str,
    table:     str = "sc_req_item",
) -> Tuple[bool, str]:
    """Append a customer-visible comment to a SNOW request item."""
    try:
        resp = requests.patch(
            f"{_base()}/table/{table}/{sys_id}",
            json={"comments": comment},
            auth=_auth(),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True, "Comment added."
    except Exception as exc:
        logger.error(f"[SNOW] add_comment({sys_id}): {exc}")
        return False, str(exc)


# ── Fulfill / close ───────────────────────────────────────────────────────────

def fulfill_request(
    sys_id:       str,
    did_number:   str,
    extension:    str = "",
    table:        str = "sc_req_item",
) -> Tuple[bool, str]:
    """
    Mark a request as fulfilled and add a customer-visible comment with
    the assigned DID details.
    """
    from app.models.app_config import AppConfig

    state      = int(AppConfig.get("SNOW_FULFILLED_STATE", "3"))
    app_name   = AppConfig.get("APP_NAME", "Orbit")

    comment = (
        f"Your Webex Calling number has been provisioned by {app_name}.\n\n"
        f"  Direct Inward Dial (DID): {did_number}\n"
        + (f"  Extension:                {extension}\n" if extension else "")
        + "\nYou will receive a separate email with your full calling details."
    )
    work_note = (
        f"[{app_name}] Auto-fulfilled. "
        f"DID {did_number} assigned"
        + (f", ext {extension}" if extension else "")
        + "."
    )

    ok1, _ = add_comment(sys_id, comment, table)
    ok2, _ = add_work_note(sys_id, work_note, table)
    ok3, m = update_request_state(sys_id, state, table)

    return (ok1 and ok3), m


def fail_request(
    sys_id:  str,
    reason:  str,
    table:   str = "sc_req_item",
) -> Tuple[bool, str]:
    """
    Mark a request as failed / closed-incomplete with a work note explaining why.
    """
    from app.models.app_config import AppConfig

    state    = int(AppConfig.get("SNOW_FAILED_STATE", "4"))
    app_name = AppConfig.get("APP_NAME", "Orbit")

    add_work_note(
        sys_id,
        f"[{app_name}] Auto-fulfillment FAILED.\nReason: {reason}\n"
        f"Manual intervention required.",
        table,
    )
    return update_request_state(sys_id, state, table)


# ── Connectivity test ─────────────────────────────────────────────────────────

def test_connection() -> Tuple[bool, str, dict]:
    """
    Validate SNOW credentials and connectivity.
    Returns (ok, message, info_dict).
    """
    instance, username, _ = _get_credentials()
    if not instance:
        return False, "ServiceNow instance URL is not configured.", {}

    try:
        resp = requests.get(
            f"{instance}/api/now/table/sys_user",
            params={"sysparm_limit": 1, "sysparm_fields": "sys_id"},
            auth=_auth(),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return True, f"Connected to {instance} as '{username}'.", {
                "instance": instance,
                "username": username,
            }
        elif resp.status_code == 401:
            return False, f"Authentication failed (HTTP 401). Check username/password.", {}
        else:
            return False, f"ServiceNow returned HTTP {resp.status_code}.", {}
    except requests.exceptions.ConnectionError:
        return False, f"Cannot reach {instance}. Check instance URL.", {}
    except Exception as exc:
        return False, f"Connection error: {exc}", {}
