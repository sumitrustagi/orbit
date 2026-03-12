"""Initial schema — all Orbit tables.

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision    = "0001"
down_revision = None
branch_labels = None
depends_on    = None


def upgrade():
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",                   sa.Integer(),     nullable=False),
        sa.Column("username",             sa.String(64),    nullable=False),
        sa.Column("email",                sa.String(255),   nullable=False),
        sa.Column("full_name",            sa.String(128),   nullable=True),
        sa.Column("password_hash",        sa.String(256),   nullable=False),
        sa.Column("role",                 sa.String(32),    nullable=False, server_default="admin"),
        sa.Column("is_active",            sa.Boolean(),     nullable=False, server_default="true"),
        sa.Column("must_change_password", sa.Boolean(),     nullable=False, server_default="false"),
        sa.Column("failed_login_count",   sa.Integer(),     nullable=False, server_default="0"),
        sa.Column("locked_until",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip",        sa.String(64),    nullable=True),
        sa.Column("notes",                sa.Text(),        nullable=True),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email",    "users", ["email"])

    # ── app_config ─────────────────────────────────────────────────────────
    op.create_table(
        "app_config",
        sa.Column("id",           sa.Integer(),  nullable=False),
        sa.Column("key",          sa.String(128),nullable=False),
        sa.Column("value",        sa.Text(),     nullable=True),
        sa.Column("is_encrypted", sa.Boolean(),  nullable=False, server_default="false"),
        sa.Column("description",  sa.String(255),nullable=True),
        sa.Column("updated_at",   sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_app_config_key", "app_config", ["key"])

    # ── did_pools ──────────────────────────────────────────────────────────
    op.create_table(
        "did_pools",
        sa.Column("id",          sa.Integer(),   nullable=False),
        sa.Column("name",        sa.String(128), nullable=False),
        sa.Column("description", sa.Text(),      nullable=True),
        sa.Column("country",     sa.String(8),   nullable=True),
        sa.Column("region",      sa.String(128), nullable=True),
        sa.Column("is_active",   sa.Boolean(),   nullable=False, server_default="true"),
        sa.Column("notes",       sa.Text(),      nullable=True),
        sa.Column("created_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",  sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # ── dids ───────────────────────────────────────────────────────────────
    op.create_table(
        "dids",
        sa.Column("id",                   sa.Integer(),   nullable=False),
        sa.Column("number",               sa.String(32),  nullable=False),
        sa.Column("e164",                 sa.String(32),  nullable=True),
        sa.Column("pool_id",              sa.Integer(),   nullable=True),
        sa.Column("status",               sa.String(32),  nullable=False, server_default="available"),
        sa.Column("country",              sa.String(8),   nullable=True),
        sa.Column("region",               sa.String(128), nullable=True),
        sa.Column("assigned_to_email",    sa.String(255), nullable=True),
        sa.Column("assigned_to_name",     sa.String(128), nullable=True),
        sa.Column("assigned_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("snow_request_number",  sa.String(64),  nullable=True),
        sa.Column("quarantine_until",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("webex_person_id",      sa.String(255), nullable=True),
        sa.Column("notes",                sa.Text(),      nullable=True),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pool_id"], ["did_pools.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.create_index("ix_dids_number",  "dids", ["number"])
    op.create_index("ix_dids_status",  "dids", ["status"])
    op.create_index("ix_dids_pool_id", "dids", ["pool_id"])

    # ── snow_requests ──────────────────────────────────────────────────────
    op.create_table(
        "snow_requests",
        sa.Column("id",                   sa.Integer(),   nullable=False),
        sa.Column("snow_number",          sa.String(64),  nullable=False),
        sa.Column("requester_email",      sa.String(255), nullable=False),
        sa.Column("requester_name",       sa.String(128), nullable=True),
        sa.Column("requested_pool_id",    sa.Integer(),   nullable=True),
        sa.Column("requested_country",    sa.String(8),   nullable=True),
        sa.Column("requested_extension",  sa.String(32),  nullable=True),
        sa.Column("status",               sa.String(32),  nullable=False, server_default="pending"),
        sa.Column("assigned_did",         sa.String(32),  nullable=True),
        sa.Column("assigned_extension",   sa.String(32),  nullable=True),
        sa.Column("retry_count",          sa.Integer(),   nullable=False, server_default="0"),
        sa.Column("failure_reason",       sa.Text(),      nullable=True),
        sa.Column("raw_payload",          sa.JSON(),      nullable=True),
        sa.Column("fulfilled_at",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requested_pool_id"], ["did_pools.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snow_number"),
    )
    op.create_index("ix_snow_requests_status",      "snow_requests", ["status"])
    op.create_index("ix_snow_requests_snow_number", "snow_requests", ["snow_number"])

    # ── call_forward_schedules ─────────────────────────────────────────────
    op.create_table(
        "call_forward_schedules",
        sa.Column("id",                     sa.Integer(),   nullable=False),
        sa.Column("name",                   sa.String(128), nullable=False),
        sa.Column("description",            sa.Text(),      nullable=True),
        sa.Column("is_active",              sa.Boolean(),   nullable=False, server_default="true"),
        sa.Column("status",                 sa.String(32),  nullable=False, server_default="active"),
        sa.Column("entity_type",            sa.String(32),  nullable=False),
        sa.Column("webex_entity_id",        sa.String(255), nullable=False),
        sa.Column("webex_entity_name",      sa.String(255), nullable=True),
        sa.Column("webex_entity_email",     sa.String(255), nullable=True),
        sa.Column("schedule_type",          sa.String(32),  nullable=False, server_default="recurring"),
        sa.Column("days_of_week",           sa.JSON(),      nullable=True),
        sa.Column("from_time",              sa.Time(),      nullable=True),
        sa.Column("to_time",                sa.Time(),      nullable=True),
        sa.Column("start_datetime",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_datetime",           sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone_name",          sa.String(64),  nullable=True, server_default="UTC"),
        sa.Column("forward_target",         sa.String(32),  nullable=False, server_default="voicemail"),
        sa.Column("forward_to",             sa.String(255), nullable=True),
        sa.Column("forward_to_name",        sa.String(255), nullable=True),
        sa.Column("is_currently_forwarded", sa.Boolean(),   nullable=False, server_default="false"),
        sa.Column("last_applied_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reverted_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by",             sa.Integer(),   nullable=True),
        sa.Column("created_at",             sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at",             sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cf_schedules_status", "call_forward_schedules", ["status"])

    # ── forward_execution_logs ─────────────────────────────────────────────
    op.create_table(
        "forward_execution_logs",
        sa.Column("id",           sa.Integer(),   nullable=False),
        sa.Column("schedule_id",  sa.Integer(),   nullable=False),
        sa.Column("action",       sa.String(16),  nullable=False),
        sa.Column("result",       sa.String(16),  nullable=False, server_default="success"),
        sa.Column("entity_id",    sa.String(255), nullable=True),
        sa.Column("entity_type",  sa.String(32),  nullable=True),
        sa.Column("forward_to",   sa.String(255), nullable=True),
        sa.Column("error_detail", sa.Text(),      nullable=True),
        sa.Column("executed_at",  sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["schedule_id"], ["call_forward_schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fwd_exec_logs_schedule_id", "forward_execution_logs", ["schedule_id"])
    op.create_index("ix_fwd_exec_logs_executed_at", "forward_execution_logs", ["executed_at"])

    # ── audit_logs ─────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id",            sa.Integer(),   nullable=False),
        sa.Column("action",        sa.String(64),  nullable=False),
        sa.Column("user_id",       sa.Integer(),   nullable=True),
        sa.Column("username",      sa.String(64),  nullable=True),
        sa.Column("user_role",     sa.String(32),  nullable=True),
        sa.Column("ip_address",    sa.String(64),  nullable=True),
        sa.Column("resource_type", sa.String(64),  nullable=True),
        sa.Column("resource_id",   sa.Integer(),   nullable=True),
        sa.Column("resource_name", sa.String(255), nullable=True),
        sa.Column("payload_before",sa.JSON(),      nullable=True),
        sa.Column("payload_after", sa.JSON(),      nullable=True),
        sa.Column("status",        sa.String(16),  nullable=True, server_default="success"),
        sa.Column("status_detail", sa.Text(),      nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_action",     "audit_logs", ["action"])
    op.create_index("ix_audit_logs_username",   "audit_logs", ["username"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_status",     "audit_logs", ["status"])

    # ── webex_cache tables ─────────────────────────────────────────────────
    for table in (
        "webex_user_cache",
        "webex_hunt_group_cache",
        "webex_call_queue_cache",
        "webex_auto_attendant_cache",
    ):
        op.create_table(
            table,
            sa.Column("id",            sa.Integer(),   nullable=False),
            sa.Column("webex_id",      sa.String(255), nullable=False),
            sa.Column("name",          sa.String(255), nullable=True),
            sa.Column("display_name",  sa.String(255), nullable=True),
            sa.Column("email",         sa.String(255), nullable=True),
            sa.Column("first_name",    sa.String(128), nullable=True),
            sa.Column("last_name",     sa.String(128), nullable=True),
            sa.Column("phone_numbers", sa.JSON(),      nullable=True),
            sa.Column("extension",     sa.String(32),  nullable=True),
            sa.Column("phone_number",  sa.String(32),  nullable=True),
            sa.Column("location_id",   sa.String(255), nullable=True),
            sa.Column("location_name", sa.String(255), nullable=True),
            sa.Column("synced_at",     sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("webex_id"),
        )
        op.create_index(f"ix_{table}_webex_id", table, ["webex_id"])


def downgrade():
    for table in (
        "webex_auto_attendant_cache",
        "webex_call_queue_cache",
        "webex_hunt_group_cache",
        "webex_user_cache",
        "audit_logs",
        "forward_execution_logs",
        "call_forward_schedules",
        "snow_requests",
        "dids",
        "did_pools",
        "app_config",
        "users",
    ):
        op.drop_table(table)
