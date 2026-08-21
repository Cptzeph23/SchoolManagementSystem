# Absolute path: SMS/smsApp/services.py
"""
Business logic / service layer.

Per spec §3 'Separation of concerns', views must not contain business logic
directly — they call into functions here. This keeps logic reusable between
Django views today and DRF API views later (§3 'API-first architecture').
"""
from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from .models import AuditLog, LoginHistory, User


def _client_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_audit(
    *,
    actor: User | None,
    action: str,
    request: HttpRequest | None = None,
    target_model: str = "",
    target_object_id: str | int = "",
    description: str = "",
    previous_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> AuditLog:
    """Single write path for the audit trail (spec §5, §27, §33, §37, §38).
    Every sensitive action (role changes, approvals, financial adjustments,
    account lock/unlock, etc.) must go through this function rather than
    writing to AuditLog directly, so the shape stays consistent."""
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_model=target_model,
        target_object_id=str(target_object_id) if target_object_id != "" else "",
        description=description,
        previous_value=previous_value,
        new_value=new_value,
        ip_address=_client_ip(request),
    )


def record_login(
    *, user: User, request: HttpRequest | None, was_successful: bool
) -> LoginHistory:
    """Spec §5 'View login history'. Called from the login view (Phase 4)."""
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""
    entry = LoginHistory.objects.create(
        user=user,
        ip_address=_client_ip(request),
        user_agent=user_agent,
        was_successful=was_successful,
    )
    log_audit(
        actor=user,
        action=AuditLog.Action.LOGIN if was_successful else AuditLog.Action.LOGIN_FAILED,
        request=request,
        target_model="User",
        target_object_id=user.pk,
        description="User logged in" if was_successful else "Failed login attempt",
    )
    return entry


def get_dashboard_url_for_role(user: User) -> str:
    """Central place mapping a User -> dashboard URL name.
    Used by the post-login router (Phase 4) and kept here, not hard-coded
    in views, so Phase 6+ dashboards only need one line added here.

    Accepts the full user (not just `role`) because `is_superuser` must
    win regardless of `role` — see User.save() docstring for why `role`
    alone can't be trusted for accounts created via createsuperuser."""
    from django.urls import reverse

    if user.is_superuser:
        return reverse("dashboard:super_admin")

    mapping = {
        User.Role.SUPER_ADMIN: "dashboard:super_admin",
        # Other roles route here as their dashboards are built
        # (Staff Admin -> Phase 6, Academic Admin -> Phase 7, etc.)
    }
    url_name = mapping.get(user.role, "dashboard:coming_soon")
    return reverse(url_name)


def correct_attendance_record(
    *,
    record: "AttendanceRecord",
    new_status: str,
    corrected_by: User,
    request: HttpRequest | None = None,
    new_notes: str | None = None,
) -> "AttendanceRecord":
    """Spec §11: 'Academic Admin can... correct attendance with appropriate
    permissions'. This is the single write path for corrections — callers
    (views, Phase 7+) must not set `record.status = ...; record.save()`
    directly, or the correction won't be captured in AuditLog (spec §5,
    §27, §33 all require before/after values for sensitive edits).

    Caller is responsible for the actual permission check (e.g.
    RoleRequiredMixin on the view) — this function only records what
    changed, it does not decide who is allowed to call it.
    """
    previous_value = {"status": record.status, "notes": record.notes}

    record.status = new_status
    if new_notes is not None:
        record.notes = new_notes
    record.recorded_by = corrected_by
    record.save(update_fields=["status", "notes", "recorded_by", "updated_at"])

    log_audit(
        actor=corrected_by,
        action=AuditLog.Action.UPDATE,
        request=request,
        target_model="AttendanceRecord",
        target_object_id=record.pk,
        description=f"Corrected attendance for {record.student} on {record.session.date}",
        previous_value=previous_value,
        new_value={"status": record.status, "notes": record.notes},
    )
    return record