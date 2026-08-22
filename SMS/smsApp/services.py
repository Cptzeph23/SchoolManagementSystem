# Absolute path: SMS/smsApp/services.py
"""
Business logic / service layer.

Per spec §3 'Separation of concerns', views must not contain business logic
directly — they call into functions here. This keeps logic reusable between
Django views today and DRF API views later (§3 'API-first architecture').
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.http import HttpRequest

from .models import Assessment, AuditLog, LoginHistory, User


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


# =============================================================================
# Phase 7 — Grading engine (spec §13). All lookups are data-driven off
# GradingScheme/GradeBand/AssessmentComponent — nothing here hard-codes a
# grade boundary or a weighting percentage.
# =============================================================================

def get_grade_for_mark(scheme, mark) -> "GradeBand | None":
    """Spec §13: resolve a numeric mark to a GradeBand under the given
    scheme. Returns None if no band covers the mark (a configuration gap
    the caller/UI should surface, not silently default from)."""
    return scheme.bands.filter(min_mark__lte=mark, max_mark__gte=mark).first()


def validate_grade_bands_no_overlap(scheme) -> list[str]:
    """Returns a list of human-readable overlap errors for the scheme's
    bands, empty if none. Not a DB constraint (cross-row check) — called
    from the admin/service layer before treating a scheme as usable."""
    bands = list(scheme.bands.order_by("min_mark"))
    errors: list[str] = []
    for i in range(len(bands) - 1):
        current, nxt = bands[i], bands[i + 1]
        if current.max_mark >= nxt.min_mark:
            errors.append(
                f"'{current.grade}' ({current.min_mark}-{current.max_mark}) overlaps "
                f"'{nxt.grade}' ({nxt.min_mark}-{nxt.max_mark})"
            )
    return errors


def compute_weighted_average(student, class_subject, term) -> dict[str, Any]:
    """Spec §12/§13: weighted total across all Assessments for a student
    in a ClassSubject/Term, using each Assessment's linked
    AssessmentComponent for weight and max_marks — no percentages are
    hard-coded here, they're read entirely from the configured structure.

    Returns a dict rather than a bare number because callers (report book,
    Phase 15) need the raw weighted score, the count of graded components,
    and whether every configured component has a mark yet.
    """
    assessments = (
        class_subject.assessments
        .filter(term=term)
        .select_related("component")
    )

    weighted_total = Decimal("0")
    weight_covered = Decimal("0")
    components_graded = 0
    components_total = assessments.count()

    for assessment in assessments:
        mark_row = assessment.marks.filter(student=student).first()
        if mark_row is None:
            continue
        component = assessment.component
        if component.max_marks <= 0:
            continue
        proportion = mark_row.marks_obtained / component.max_marks
        weighted_total += proportion * component.weight_percentage
        weight_covered += component.weight_percentage
        components_graded += 1

    return {
        "weighted_total": weighted_total.quantize(Decimal("0.01")),
        "weight_covered": weight_covered.quantize(Decimal("0.01")),
        "components_graded": components_graded,
        "components_total": components_total,
        "is_complete": components_graded == components_total and components_total > 0,
    }


# =============================================================================
# Phase 8 — Result Processing Workflow (spec §14)
# DRAFT -> SUBMITTED -> REVIEWED -> VERIFIED -> APPROVED -> PUBLISHED.
# Every transition is a separate, narrow function so each pipeline stage
# can be permission-checked independently by the calling view (e.g. only
# a Class Teacher may call review_assessment, only Academic Admin may call
# verify_assessment) — this module does not decide who is allowed to call
# it, only that the *sequence* is respected and every step is audited.
# =============================================================================

_WORKFLOW_ORDER = [
    "DRAFT", "SUBMITTED", "REVIEWED", "VERIFIED", "APPROVED", "PUBLISHED",
]


def _require_status(assessment, expected: str) -> None:
    if assessment.workflow_status != expected:
        raise ValueError(
            f"Cannot perform this transition from status "
            f"'{assessment.workflow_status}' — expected '{expected}'."
        )


def transition_assessment_workflow(
    *,
    assessment: "Assessment",
    to_status: str,
    actor: User,
    request: HttpRequest | None = None,
) -> "Assessment":
    """Single write path for every workflow stage change. `to_status` must
    be the next status in _WORKFLOW_ORDER (no skipping stages, no going
    backwards except via explicit rejection — see reject_assessment).

    Enforces spec §9: 'Teachers must not be able to approve their own
    final results' — the actor who submitted an assessment cannot also
    be the one who approves it.
    """
    from django.utils import timezone

    current_index = _WORKFLOW_ORDER.index(assessment.workflow_status)
    try:
        target_index = _WORKFLOW_ORDER.index(to_status)
    except ValueError:
        raise ValueError(f"'{to_status}' is not a valid forward workflow status.")

    if target_index != current_index + 1:
        raise ValueError(
            f"Cannot jump from '{assessment.workflow_status}' to '{to_status}' — "
            f"stages must be completed in order."
        )

    if to_status == Assessment.WorkflowStatus.APPROVED and assessment.submitted_by_id == actor.pk:
        raise PermissionError(
            "A teacher cannot approve their own submitted results — "
            "independent approval is required (spec §9)."
        )

    now = timezone.now()
    field_map = {
        "SUBMITTED": ("submitted_by", "submitted_at"),
        "REVIEWED": ("reviewed_by", "reviewed_at"),
        "VERIFIED": ("verified_by", "verified_at"),
        "APPROVED": ("approved_by", "approved_at"),
        "PUBLISHED": ("published_by", "published_at"),
    }
    actor_field, timestamp_field = field_map[to_status]
    setattr(assessment, actor_field, actor)
    setattr(assessment, timestamp_field, now)
    assessment.workflow_status = to_status
    if to_status == Assessment.WorkflowStatus.PUBLISHED:
        assessment.is_published = True

    assessment.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.PUBLISH if to_status == "PUBLISHED" else AuditLog.Action.APPROVE,
        request=request,
        target_model="Assessment",
        target_object_id=assessment.pk,
        description=f"Assessment moved to '{to_status}'",
        previous_value={"workflow_status": _WORKFLOW_ORDER[current_index]},
        new_value={"workflow_status": to_status},
    )
    return assessment


def reject_assessment(
    *, assessment: "Assessment", actor: User, reason: str, request: HttpRequest | None = None
) -> "Assessment":
    """Sends an assessment back to DRAFT for correction, from any
    in-progress stage (not from PUBLISHED — use amendment requests
    instead, since published results must not be silently reopened)."""
    if assessment.workflow_status in (
        Assessment.WorkflowStatus.DRAFT, Assessment.WorkflowStatus.PUBLISHED,
    ):
        raise ValueError(
            f"Cannot reject an assessment in '{assessment.workflow_status}' status."
        )

    previous_status = assessment.workflow_status
    assessment.workflow_status = Assessment.WorkflowStatus.DRAFT
    assessment.save(update_fields=["workflow_status", "updated_at"])

    log_audit(
        actor=actor,
        action=AuditLog.Action.UPDATE,
        request=request,
        target_model="Assessment",
        target_object_id=assessment.pk,
        description=f"Assessment rejected and returned to Draft: {reason}",
        previous_value={"workflow_status": previous_status},
        new_value={"workflow_status": "DRAFT"},
    )
    return assessment


def request_result_amendment(
    *,
    assessment_mark: "AssessmentMark",
    reason: str,
    proposed_mark,
    requested_by: User,
    request: HttpRequest | None = None,
) -> "ResultAmendmentRequest":
    """Spec §14: the only way to change a mark once its Assessment has
    been PUBLISHED. Captures original_mark as a snapshot so the audit
    trail is accurate even if the mark changes again before this is
    reviewed."""
    from .models import ResultAmendmentRequest

    amendment = ResultAmendmentRequest.objects.create(
        assessment_mark=assessment_mark,
        reason=reason,
        original_mark=assessment_mark.marks_obtained,
        proposed_mark=proposed_mark,
        requested_by=requested_by,
    )
    log_audit(
        actor=requested_by,
        action=AuditLog.Action.OTHER,
        request=request,
        target_model="ResultAmendmentRequest",
        target_object_id=amendment.pk,
        description=reason,
        previous_value={"mark": str(assessment_mark.marks_obtained)},
        new_value={"proposed_mark": str(proposed_mark)},
    )
    return amendment


def decide_result_amendment(
    *,
    amendment: "ResultAmendmentRequest",
    approve: bool,
    reviewed_by: User,
    comment: str = "",
    request: HttpRequest | None = None,
) -> "ResultAmendmentRequest":
    """Applies or rejects a pending amendment. Approving is the *only*
    code path permitted to mutate marks.marks_obtained on a mark whose
    Assessment is already PUBLISHED (spec §14 'prevent unrestricted
    modification')."""
    from django.utils import timezone
    from .models import ResultAmendmentRequest

    if amendment.status != ResultAmendmentRequest.Status.PENDING:
        raise ValueError("This amendment request has already been decided.")

    amendment.reviewed_by = reviewed_by
    amendment.reviewed_at = timezone.now()
    amendment.review_comment = comment

    if approve:
        amendment.status = ResultAmendmentRequest.Status.APPROVED
        mark = amendment.assessment_mark
        previous_marks = mark.marks_obtained
        mark.marks_obtained = amendment.proposed_mark
        mark.save(update_fields=["marks_obtained", "updated_at"], _bypass_publish_lock=True)

        log_audit(
            actor=reviewed_by,
            action=AuditLog.Action.UPDATE,
            request=request,
            target_model="AssessmentMark",
            target_object_id=mark.pk,
            description=f"Amendment approved: {amendment.reason}",
            previous_value={"marks_obtained": str(previous_marks)},
            new_value={"marks_obtained": str(mark.marks_obtained)},
        )
    else:
        amendment.status = ResultAmendmentRequest.Status.REJECTED
        log_audit(
            actor=reviewed_by,
            action=AuditLog.Action.OTHER,
            request=request,
            target_model="ResultAmendmentRequest",
            target_object_id=amendment.pk,
            description=f"Amendment rejected: {comment}",
        )

    amendment.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"])
    return amendment