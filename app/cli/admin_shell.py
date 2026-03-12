"""
Flask CLI admin commands.

Usage:
  flask admin create-admin
  flask admin reset-password <username>
  flask admin list-users
  flask admin seed-config
  flask admin sync-webex
  flask admin purge-audit --days <N>
  flask admin show-config
  flask admin set-config <key> <value> [--encrypted]
  flask admin test-connections
"""
import click
import sys
from datetime import datetime, timezone, timedelta
from flask import Blueprint

admin_cli = Blueprint("admin", __name__)


# ── create-admin ──────────────────────────────────────────────────────────────

@admin_cli.cli.command("create-admin")
@click.option("--username",  prompt=True,                help="Username")
@click.option("--email",     prompt=True,                help="Email address")
@click.option("--password",  prompt=True, hide_input=True,
              confirmation_prompt=True,                   help="Password")
@click.option("--role",      default="superadmin",
              type=click.Choice(["superadmin","admin","readonly"]),
              help="Role (default: superadmin)")
def create_admin(username, email, password, role):
    """Create a new admin user account."""
    from app.extensions import db
    from app.models.user import User, UserRole

    username = username.strip().lower()
    email    = email.strip().lower()

    existing = User.query.filter(
        (User.username == username) | (User.email == email)
    ).first()
    if existing:
        click.echo(
            click.style(
                f"ERROR: User with username '{username}' or email '{email}' already exists.",
                fg="red"
            )
        )
        sys.exit(1)

    user = User(
        username  = username,
        email     = email,
        role      = UserRole(role),
        is_active = True,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    click.echo(
        click.style(
            f"✓ Created {role} user '{username}' (id={user.id})",
            fg="green"
        )
    )


# ── reset-password ────────────────────────────────────────────────────────────

@admin_cli.cli.command("reset-password")
@click.argument("username")
@click.option("--password", prompt=True, hide_input=True,
              confirmation_prompt=True, help="New password")
@click.option("--force-change/--no-force-change", default=True,
              help="Require password change on next login")
def reset_password(username, password, force_change):
    """Reset the password for an admin user."""
    from app.extensions import db
    from app.models.user import User

    user = User.query.filter_by(username=username.lower()).first()
    if not user:
        click.echo(click.style(f"ERROR: User '{username}' not found.", fg="red"))
        sys.exit(1)

    user.set_password(password)
    user.must_change_password = force_change
    user.failed_login_count   = 0
    user.locked_until         = None
    db.session.commit()

    click.echo(
        click.style(
            f"✓ Password reset for '{username}'. "
            f"{'User must change on next login.' if force_change else ''}",
            fg="green"
        )
    )


# ── list-users ────────────────────────────────────────────────────────────────

@admin_cli.cli.command("list-users")
@click.option("--role", default="", help="Filter by role")
def list_users(role):
    """List all admin user accounts."""
    from app.models.user import User, UserRole

    q = User.query.order_by(User.username.asc())
    if role:
        try:
            q = q.filter_by(role=UserRole(role))
        except ValueError:
            click.echo(f"Unknown role: {role}")
            sys.exit(1)

    users = q.all()
    click.echo(f"\n{'ID':<5} {'Username':<20} {'Email':<35} {'Role':<14} {'Active':<8} {'Last Login'}")
    click.echo("-" * 100)
    for u in users:
        last = (
            u.last_login_at.strftime("%Y-%m-%d %H:%M")
            if u.last_login_at else "never"
        )
        active_str = click.style("yes", fg="green") if u.is_active else click.style("no", fg="red")
        click.echo(
            f"{u.id:<5} {u.username:<20} {u.email:<35} "
            f"{u.role.value:<14} {active_str:<8} {last}"
        )
    click.echo(f"\nTotal: {len(users)} users")


# ── seed-config ───────────────────────────────────────────────────────────────

@admin_cli.cli.command("seed-config")
@click.option("--force/--no-force", default=False,
              help="Overwrite existing keys")
def seed_config(force):
    """Seed default AppConfig values into the database."""
    from app.models.app_config import AppConfig

    DEFAULTS = [
        ("APP_NAME",                "Orbit",    False),
        ("APP_VERSION",             "1.0.0",    False),
        ("PRIMARY_COLOR",           "#1E40AF",  False),
        ("ACCENT_COLOR",            "#3B82F6",  False),
        ("SESSION_TIMEOUT_MINUTES", "30",       False),
        ("MAINTENANCE_MODE",        "false",    False),
        ("ITEMS_PER_PAGE",          "25",       False),
        ("SNOW_AUTO_FULFILL",       "true",     False),
        ("SNOW_FULFILLED_STATE",    "3",        False),
        ("SNOW_FAILED_STATE",       "4",        False),
        ("SNOW_SEND_WELCOME_EMAIL", "true",     False),
        ("SNOW_SEND_DID_EMAIL",     "true",     False),
        ("WEBEX_CALLING_ENABLED",   "true",     False),
        ("WEBEX_CACHE_TTL",         "300",      False),
        ("WEBEX_TIMEOUT",           "15",       False),
        ("SMTP_PORT",               "587",      False),
        ("SMTP_USE_TLS",            "true",     False),
        ("SMTP_USE_SSL",            "false",    False),
        ("SMTP_SENDER_NAME",        "Orbit",    False),
        ("MIN_PASSWORD_LENGTH",     "10",       False),
        ("REQUIRE_UPPERCASE",       "true",     False),
        ("REQUIRE_LOWERCASE",       "true",     False),
        ("REQUIRE_DIGIT",           "true",     False),
        ("REQUIRE_SPECIAL",         "true",     False),
        ("MAX_LOGIN_ATTEMPTS",      "5",        False),
        ("LOCKOUT_DURATION_MINUTES","15",       False),
        ("AUDIT_RETENTION_DAYS",    "365",      False),
        ("ALLOW_API_TOKENS",        "true",     False),
        ("FORCE_HTTPS",             "true",     False),
    ]

    seeded = 0
    for key, value, encrypted in DEFAULTS:
        existing = AppConfig.query.filter_by(key=key).first()
        if existing and not force:
            click.echo(f"  skip  {key} (already set)")
            continue
        AppConfig.set(key, value, encrypted=encrypted)
        click.echo(click.style(f"  set   {key} = {value}", fg="green"))
        seeded += 1

    click.echo(f"\n✓ Seeded {seeded} config values.")


# ── sync-webex ────────────────────────────────────────────────────────────────

@admin_cli.cli.command("sync-webex")
@click.option("--users/--no-users",       default=True,  help="Sync users")
@click.option("--hunt-groups/--no-hunt-groups", default=True, help="Sync hunt groups")
@click.option("--queues/--no-queues",     default=True,  help="Sync call queues")
def sync_webex(users, hunt_groups, queues):
    """Force a full Webex entity sync (normally run by Celery beat)."""
    from app.tasks.webex import (
        sync_webex_users, sync_hunt_groups, sync_call_queues
    )

    if users:
        click.echo("Syncing Webex users…")
        try:
            sync_webex_users.apply()
            click.echo(click.style("  ✓ Users synced.", fg="green"))
        except Exception as exc:
            click.echo(click.style(f"  ✗ {exc}", fg="red"))

    if hunt_groups:
        click.echo("Syncing hunt groups…")
        try:
            sync_hunt_groups.apply()
            click.echo(click.style("  ✓ Hunt groups synced.", fg="green"))
        except Exception as exc:
            click.echo(click.style(f"  ✗ {exc}", fg="red"))

    if queues:
        click.echo("Syncing call queues…")
        try:
            sync_call_queues.apply()
            click.echo(click.style("  ✓ Call queues synced.", fg="green"))
        except Exception as exc:
            click.echo(click.style(f"  ✗ {exc}", fg="red"))

    click.echo("Done.")


# ── purge-audit ───────────────────────────────────────────────────────────────

@admin_cli.cli.command("purge-audit")
@click.option("--days", default=365,
              help="Delete audit logs older than N days (default: 365)")
@click.option("--confirm/--no-confirm", default=False,
              help="Skip confirmation prompt")
def purge_audit(days, confirm):
    """Delete audit log entries older than N days."""
    from app.extensions import db
    from app.models.audit import AuditLog

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count  = AuditLog.query.filter(AuditLog.created_at < cutoff).count()

    if count == 0:
        click.echo(f"No audit entries older than {days} days found.")
        return

    if not confirm:
        click.confirm(
            f"This will permanently delete {count} audit entries "
            f"older than {days} days. Continue?",
            abort=True
        )

    deleted = AuditLog.query.filter(AuditLog.created_at < cutoff).delete()
    db.session.commit()
    click.echo(click.style(f"✓ Deleted {deleted} audit entries.", fg="green"))


# ── show-config ───────────────────────────────────────────────────────────────

@admin_cli.cli.command("show-config")
@click.option("--show-secrets/--no-show-secrets", default=False,
              help="Reveal encrypted values")
def show_config(show_secrets):
    """Display all AppConfig key-value pairs."""
    from app.models.app_config import AppConfig
    from app.utils.crypto import decrypt

    rows = AppConfig.query.order_by(AppConfig.key.asc()).all()
    click.echo(f"\n{'Key':<35} {'Value':<40} Encrypted")
    click.echo("-" * 80)
    for row in rows:
        if row.is_encrypted:
            val = decrypt(row.value) if show_secrets else "***"
        else:
            val = row.value or ""
        enc = click.style("yes", fg="yellow") if row.is_encrypted else "no"
        click.echo(f"{row.key:<35} {val:<40} {enc}")
    click.echo(f"\nTotal: {len(rows)} keys")


# ── set-config ────────────────────────────────────────────────────────────────

@admin_cli.cli.command("set-config")
@click.argument("key")
@click.argument("value")
@click.option("--encrypted/--no-encrypted", default=False,
              help="Encrypt the value at rest")
def set_config(key, value, encrypted):
    """Set a single AppConfig key-value pair from the CLI."""
    from app.models.app_config import AppConfig
    from app.utils.crypto import encrypt

    stored = encrypt(value) if encrypted else value
    AppConfig.set(key, stored, encrypted=encrypted)
    click.echo(
        click.style(
            f"✓ Set {key} = {'[encrypted]' if encrypted else value}",
            fg="green"
        )
    )


# ── test-connections ──────────────────────────────────────────────────────────

@admin_cli.cli.command("test-connections")
def test_connections():
    """Test Webex, ServiceNow, and SMTP connectivity."""
    click.echo("\nTesting connections…\n")

    # Webex
    click.echo("Webex API:")
    try:
        from app.services.webex_service import get_webex_client
        webex = get_webex_client()
        org   = webex.org
        click.echo(
            click.style(
                f"  ✓ Connected — org: {getattr(org,'name','unknown')}",
                fg="green"
            )
        )
    except Exception as exc:
        click.echo(click.style(f"  ✗ {exc}", fg="red"))

    # ServiceNow
    click.echo("ServiceNow:")
    try:
        from app.services.snow_service import test_connection
        ok, msg = test_connection()
        sym = "✓" if ok else "✗"
        col = "green" if ok else "red"
        click.echo(click.style(f"  {sym} {msg}", fg=col))
    except Exception as exc:
        click.echo(click.style(f"  ✗ {exc}", fg="red"))

    # SMTP
    click.echo("SMTP:")
    try:
        from app.services.email_service import test_smtp_connection
        ok, msg = test_smtp_connection()
        sym = "✓" if ok else "✗"
        col = "green" if ok else "red"
        click.echo(click.style(f"  {sym} {msg}", fg=col))
    except Exception as exc:
        click.echo(click.style(f"  ✗ {exc}", fg="red"))

    click.echo("")
