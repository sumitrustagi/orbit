"""
Webex service — singleton wxcadm client factory.
All routes and tasks import get_webex_client() instead of
instantiating wxcadm directly, so the token is always fresh
from AppConfig/environment.
"""
import os
import logging
from functools import lru_cache
from typing import Optional

import wxcadm

from app.utils.crypto import decrypt

logger = logging.getLogger(__name__)

_webex_client: Optional[wxcadm.Webex] = None


def get_webex_client(force_refresh: bool = False) -> wxcadm.Webex:
    """
    Return a cached wxcadm.Webex instance.
    Re-initialises if force_refresh=True or if token has changed.
    """
    global _webex_client

    token = _get_token()
    if not token:
        raise RuntimeError(
            "WEBEX_ACCESS_TOKEN is not configured. "
            "Complete the first-time setup wizard."
        )

    if _webex_client is None or force_refresh:
        logger.info("[Webex] Initialising wxcadm client.")
        _webex_client = wxcadm.Webex(access_token=token)
        logger.info(
            f"[Webex] Connected. Org: "
            f"{getattr(_webex_client.org, 'name', 'Unknown')}"
        )

    return _webex_client


def refresh_webex_client() -> wxcadm.Webex:
    """Force re-initialisation (e.g. after token rotation)."""
    return get_webex_client(force_refresh=True)


def test_webex_token(token: str) -> tuple[bool, str, dict]:
    """
    Validate a token without touching the cached client.
    Returns (success, message, info_dict).
    """
    import requests
    try:
        resp = requests.get(
            "https://webexapis.com/v1/people/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.status_code == 200:
            me   = resp.json()
            oid  = me.get("orgId", "")
            org_resp = requests.get(
                f"https://webexapis.com/v1/organizations/{oid}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            org_name = (
                org_resp.json().get("displayName", "Unknown")
                if org_resp.ok else "Unknown"
            )
            return True, f"Token valid — {me.get('displayName')}", {
                "display_name": me.get("displayName", ""),
                "email":        me.get("emails", [""])[0],
                "org_id":       oid,
                "org_name":     org_name,
            }
        elif resp.status_code == 401:
            return False, "Token invalid or expired (HTTP 401).", {}
        else:
            return False, f"Webex API returned HTTP {resp.status_code}.", {}
    except requests.ConnectionError:
        return False, "Cannot reach Webex API. Check internet connectivity.", {}
    except Exception as exc:
        return False, f"Token validation error: {exc}", {}


def _get_token() -> str:
    """
    Retrieve the Webex token — tries AppConfig DB first (setup wizard saves
    it there), falls back to environment variable.
    """
    try:
        from app.models.app_config import AppConfig
        token = AppConfig.get("WEBEX_ACCESS_TOKEN", "")
        if token:
            return token
    except Exception:
        pass

    raw = os.environ.get("WEBEX_ACCESS_TOKEN", "")
    return decrypt(raw) if raw else ""
