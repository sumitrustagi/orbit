"""
Import all models here so Alembic can detect them for autogenerate.
"""
from .user         import User, UserRole
from .app_config   import AppConfig
from .did          import DIDPool, DIDAssignment, DIDStatus
from .audit        import AuditLog
from .call_forward import CallForwardSchedule
from .snow         import ServiceNowRequest

__all__ = [
    "User", "UserRole",
    "AppConfig",
    "DIDPool", "DIDAssignment", "DIDStatus",
    "AuditLog",
    "CallForwardSchedule",
    "ServiceNowRequest",
]
