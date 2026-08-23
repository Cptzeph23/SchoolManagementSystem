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

from .models import Assessment, AuditLog, ClassSubject, LoginHistory, Student, Term, User


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


def compute_weighted_average(
    student, class_subject, term, *, published_only: bool = False
) -> dict[str, Any]:
    """Spec §12/§13: weighted total across all Assessments for a student
    in a ClassSubject/Term, using each Assessment's linked
    AssessmentComponent for weight and max_marks — no percentages are
    hard-coded here, they're read entirely from the configured structure.

    `published_only=True` restricts to Assessments whose workflow has
    reached PUBLISHED (spec §14) — used by transcript generation (Phase
    10), which must never include draft/unapproved marks in an official
    academic record. Report books (Phase 9) intentionally leave this
    False, since a term-in-progress report legitimately shows draft marks.

    Returns a dict rather than a bare number because callers (report book,
    Phase 15) need the raw weighted score, the count of graded components,
    and whether every configured component has a mark yet.
    """
    assessments = (
        class_subject.assessments
        .filter(term=term)
        .select_related("component")
    )
    if published_only:
        assessments = assessments.filter(workflow_status=Assessment.WorkflowStatus.PUBLISHED)

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


# =============================================================================
# Phase 9 — Report Book System (spec §15). All layout lives in
# templates/reports/*.html; nothing here hard-codes HTML/positioning —
# this module only assembles the data dict the template renders.
# =============================================================================

def compute_class_term_rankings(*, class_group, term) -> dict[int, dict[str, Any]]:
    """Spec §13: 'Make ranking configurable because some schools may
    choose not to rank students' — callers must check
    class_group.school.enable_position_ranking before using this for
    display; it's computed unconditionally here so the check stays a
    presentation decision, not a data-availability one.

    Returns {student_id: {"average": Decimal, "position": int}} across
    every student currently in the class, ranked by their average
    percentage across all of their ClassSubjects for the term.
    """
    students = Student.objects.filter(current_class=class_group, is_active=True)
    averages: list[tuple[int, Decimal]] = []

    for student in students:
        class_subjects = ClassSubject.objects.filter(
            enrollments__student=student, enrollments__academic_year=term.academic_year
        ).distinct()
        if not class_subjects:
            continue
        totals = [
            compute_weighted_average(student, cs, term)["weighted_total"]
            for cs in class_subjects
        ]
        if totals:
            averages.append((student.pk, sum(totals) / len(totals)))

    averages.sort(key=lambda pair: pair[1], reverse=True)

    results: dict[int, dict[str, Any]] = {}
    for position, (student_id, average) in enumerate(averages, start=1):
        results[student_id] = {"average": average, "position": position}
    return results


def assemble_report_data(*, student: "Student", term: "Term") -> dict[str, Any]:
    """Spec §15: gathers every field the report layout needs — school
    identity, student identity, per-subject marks/grades, attendance,
    position (if enabled) — into one plain dict. The template decides how
    to lay it out; this function never renders HTML or decides layout."""
    from .models import AttendanceRecord, GradingScheme

    school = student.school
    class_subjects = ClassSubject.objects.filter(
        enrollments__student=student, enrollments__academic_year=term.academic_year
    ).distinct().select_related("subject")

    grading_scheme = GradingScheme.objects.filter(school=school, is_default=True).first()

    subject_rows = []
    percentage_totals = []
    for class_subject in class_subjects:
        summary = compute_weighted_average(student, class_subject, term)
        band = None
        if grading_scheme and summary["weight_covered"] > 0:
            band = get_grade_for_mark(grading_scheme, summary["weighted_total"])
        subject_rows.append(
            {
                "subject": class_subject.subject.name,
                "score": summary["weighted_total"],
                "grade": band.grade if band else "-",
                "grade_point": band.grade_point if band else None,
                "is_complete": summary["is_complete"],
            }
        )
        if summary["weight_covered"] > 0:
            percentage_totals.append(summary["weighted_total"])

    average = (
        (sum(percentage_totals) / len(percentage_totals)).quantize(Decimal("0.01"))
        if percentage_totals else None
    )

    ranking = None
    if school.enable_position_ranking and student.current_class_id:
        rankings = compute_class_term_rankings(class_group=student.current_class, term=term)
        ranking = rankings.get(student.pk)

    attendance_qs = AttendanceRecord.objects.filter(
        student=student, session__term=term
    )
    attendance_summary = {
        "present": attendance_qs.filter(status="PRESENT").count(),
        "absent": attendance_qs.filter(status="ABSENT").count(),
        "late": attendance_qs.filter(status="LATE").count(),
        "excused": attendance_qs.filter(status="EXCUSED").count(),
    }

    return {
        "school": school,
        "student": student,
        "class_group": student.current_class,
        "stream": student.current_stream,
        "academic_year": term.academic_year,
        "term": term,
        "subject_rows": subject_rows,
        "average": average,
        "position": ranking["position"] if ranking else None,
        "class_size": len(
            [k for k in (compute_class_term_rankings(
                class_group=student.current_class, term=term
            ) if student.current_class_id else {})]
        ) if ranking else None,
        "attendance_summary": attendance_summary,
    }


def render_report_html(*, report_card: "ReportCard") -> str:
    """Renders report_card's configured template with freshly assembled
    data. Template choice and which sections to show come entirely from
    ReportTemplate (spec §15 'Allow report templates to be configurable.
    Do not hard-code the report layout into business logic') — this
    function contains no layout decisions itself."""
    from django.template.loader import render_to_string

    template_paths = {
        "DEFAULT": "reports/default.html",
    }
    template_path = template_paths[report_card.template.template_key]

    context = assemble_report_data(student=report_card.student, term=report_card.term)
    context.update(
        {
            "template_config": report_card.template,
            "class_teacher_comment": report_card.class_teacher_comment,
            "principal_comment": report_card.principal_comment,
        }
    )
    return render_to_string(template_path, context)


def generate_report_pdf(
    *, report_card: "ReportCard", generated_by: User, request: HttpRequest | None = None
) -> "ReportCard":
    """Spec §15 'PDF report' / 'Downloadable report'. Renders via
    render_report_html() then converts with WeasyPrint (HTML/CSS -> PDF),
    so the PDF and the on-screen HTML report always come from the exact
    same template and data-assembly code — no separate PDF-only layout
    to fall out of sync."""
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from weasyprint import HTML

    html_content = render_report_html(report_card=report_card)
    pdf_bytes = HTML(string=html_content).write_pdf()

    filename = f"report_{report_card.student.admission_number}_{report_card.term_id}.pdf"
    report_card.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    report_card.generated_by = generated_by
    report_card.generated_at = timezone.now()
    report_card.save()

    log_audit(
        actor=generated_by,
        action=AuditLog.Action.OTHER,
        request=request,
        target_model="ReportCard",
        target_object_id=report_card.pk,
        description=f"Generated report PDF for {report_card.student}",
    )
    return report_card


def generate_batch_reports(
    *,
    class_group,
    term: "Term",
    template: "ReportTemplate",
    generated_by: User,
    request: HttpRequest | None = None,
) -> list["ReportCard"]:
    """Spec §15 'Batch reports'. Creates/updates one ReportCard per active
    student in the class and generates each PDF. Returns the list so the
    calling view can present a summary/zip download."""
    from .models import ReportCard as ReportCardModel

    cards = []
    for student in Student.objects.filter(current_class=class_group, is_active=True):
        card, _ = ReportCardModel.objects.get_or_create(
            student=student, term=term, template=template
        )
        generate_report_pdf(report_card=card, generated_by=generated_by, request=request)
        cards.append(card)
    return cards


# =============================================================================
# Phase 10 — Transcript System (spec §16). See Transcript/TranscriptEntry
# docstrings in models.py for why entries are snapshotted rather than
# recomputed live, and for the "secure PDF" interpretation.
# =============================================================================

def generate_transcript(
    *, student: "Student", generated_by: User, request: HttpRequest | None = None
) -> "Transcript":
    """Builds a full cumulative transcript from every PUBLISHED assessment
    across every term/class_subject the student has been enrolled in,
    snapshots it into TranscriptEntry rows, computes GPA/CGPA, renders the
    PDF, and stamps a verification_code + content_hash."""
    import hashlib

    from django.core.files.base import ContentFile
    from django.template.loader import render_to_string
    from django.utils import timezone
    from weasyprint import HTML

    from .models import ClassSubject as ClassSubjectModel, GradingScheme, Transcript as TranscriptModel

    school = student.school
    grading_scheme = GradingScheme.objects.filter(school=school, is_default=True).first()

    class_subjects = (
        ClassSubjectModel.objects.filter(enrollments__student=student)
        .distinct()
        .select_related("subject", "class_group")
    )

    entry_rows = []
    latest_term = None
    for class_subject in class_subjects:
        terms = Term.objects.filter(
            assessments__class_subject=class_subject,
            assessments__marks__student=student,
            assessments__workflow_status=Assessment.WorkflowStatus.PUBLISHED,
        ).distinct()

        for term in terms:
            summary = compute_weighted_average(
                student, class_subject, term, published_only=True
            )
            if summary["components_graded"] == 0:
                continue

            band = None
            if grading_scheme:
                band = get_grade_for_mark(grading_scheme, summary["weighted_total"])

            entry_rows.append(
                {
                    "subject": class_subject.subject,
                    "subject_name": class_subject.subject.name,
                    "academic_year_label": term.academic_year.name,
                    "term_label": term.name,
                    "score": summary["weighted_total"],
                    "grade": band.grade if band else "",
                    "grade_point": band.grade_point if band else None,
                    "credit_hours": class_subject.subject.credit_hours,
                    "term_obj": term,
                }
            )
            if latest_term is None or term.start_date > latest_term.start_date:
                latest_term = term

    def _grade_points(rows):
        return [r["grade_point"] for r in rows if r["grade_point"] is not None]

    cgpa = None
    all_points = _grade_points(entry_rows)
    if all_points:
        weighted_sum = Decimal("0")
        weight_sum = Decimal("0")
        for row in entry_rows:
            if row["grade_point"] is None:
                continue
            credit = row["credit_hours"] or Decimal("1")
            weighted_sum += row["grade_point"] * credit
            weight_sum += credit
        cgpa = (weighted_sum / weight_sum).quantize(Decimal("0.01")) if weight_sum else None

    gpa = None
    if latest_term is not None:
        latest_points = _grade_points(
            [r for r in entry_rows if r["term_obj"] == latest_term]
        )
        if latest_points:
            gpa = (sum(latest_points) / len(latest_points)).quantize(Decimal("0.01"))

    graduation_status = TranscriptModel.GraduationStatus.IN_PROGRESS
    if student.status == Student.Status.GRADUATED:
        graduation_status = TranscriptModel.GraduationStatus.GRADUATED
    elif student.status in (
        Student.Status.WITHDRAWN, Student.Status.EXPELLED, Student.Status.TRANSFERRED,
    ):
        graduation_status = TranscriptModel.GraduationStatus.NOT_GRADUATED

    transcript = TranscriptModel.objects.create(
        student=student,
        generated_by=generated_by,
        academic_status=student.status,
        graduation_status=graduation_status,
        gpa=gpa,
        cgpa=cgpa,
    )

    for row in entry_rows:
        transcript.entries.create(
            subject=row["subject"],
            subject_name=row["subject_name"],
            academic_year_label=row["academic_year_label"],
            term_label=row["term_label"],
            score=row["score"],
            grade=row["grade"],
            grade_point=row["grade_point"],
            credit_hours=row["credit_hours"],
        )

    # Content hash over a canonical representation of what was issued, so
    # a later dispute can prove whether a PDF matches what was generated.
    canonical = "|".join(
        f"{r['subject_name']}:{r['term_label']}:{r['score']}:{r['grade']}"
        for r in sorted(entry_rows, key=lambda r: (r["academic_year_label"], r["term_label"], r["subject_name"]))
    )
    canonical += f"|gpa={gpa}|cgpa={cgpa}|status={student.status}"
    transcript.content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    transcript.save(update_fields=["content_hash"])

    html_content = render_to_string(
        "reports/transcript.html",
        {
            "school": school,
            "student": student,
            "transcript": transcript,
            "entries": transcript.entries.all(),
        },
    )
    pdf_bytes = HTML(string=html_content).write_pdf()
    transcript.pdf_file.save(
        f"transcript_{student.admission_number}_{transcript.pk}.pdf",
        ContentFile(pdf_bytes), save=True,
    )

    log_audit(
        actor=generated_by,
        action=AuditLog.Action.OTHER,
        request=request,
        target_model="Transcript",
        target_object_id=transcript.pk,
        description=f"Generated transcript for {student}",
    )
    return transcript


def verify_transcript(verification_code) -> dict[str, Any]:
    """Spec §16 'Generate secure PDF documents' — the verification half of
    that: given the UUID printed on an issued transcript, confirm it's
    genuine without exposing the full academic record to whoever holds
    the code."""
    from .models import Transcript as TranscriptModel

    transcript = TranscriptModel.objects.filter(verification_code=verification_code).first()
    if transcript is None:
        return {"valid": False}

    return {
        "valid": True,
        "student_name": transcript.student.user.get_full_name()
        or transcript.student.user.username,
        "admission_number": transcript.student.admission_number,
        "generated_at": transcript.generated_at,
        "cgpa": transcript.cgpa,
        "graduation_status": transcript.get_graduation_status_display(),
    }


# =============================================================================
# Phase 11 — LMS Module (spec §10)
# =============================================================================

def submit_assignment(
    *,
    assignment: "Assignment",
    student: Student,
    submitted_file=None,
    submitted_text: str = "",
    request: HttpRequest | None = None,
):
    """Spec §10 'Upload submissions' / 'Resubmit where permitted'. Single
    write path for both first submission and resubmission — enforces:
    - a first submission is always allowed (while published);
    - a second+ submission requires assignment.allow_resubmission;
    - `is_late` is computed against the deadline at submission time and
      never recomputed later, so it stays an honest historical record
      even if the deadline is edited afterward.
    """
    from django.utils import timezone

    from .models import AssignmentSubmission

    existing = AssignmentSubmission.objects.filter(
        assignment=assignment, student=student
    ).first()

    if existing is not None and not assignment.allow_resubmission:
        raise ValueError(
            "This assignment does not allow resubmission; a submission "
            "already exists for this student."
        )

    now = timezone.now()
    is_late = now > assignment.deadline

    if existing is None:
        submission = AssignmentSubmission.objects.create(
            assignment=assignment, student=student,
            submitted_file=submitted_file, submitted_text=submitted_text,
            is_late=is_late, status=AssignmentSubmission.Status.SUBMITTED,
        )
    else:
        previous_value = {
            "submitted_text": existing.submitted_text,
            "attempt_number": existing.attempt_number,
        }
        existing.submitted_file = submitted_file
        existing.submitted_text = submitted_text
        existing.attempt_number += 1
        existing.is_late = is_late
        existing.status = AssignmentSubmission.Status.RESUBMITTED
        # A resubmission supersedes any prior grade — re-grading is required.
        existing.marks_obtained = None
        existing.feedback = ""
        existing.graded_by = None
        existing.graded_at = None
        existing.save()
        submission = existing

        log_audit(
            actor=student.user, action=AuditLog.Action.UPDATE, request=request,
            target_model="AssignmentSubmission", target_object_id=submission.pk,
            description=f"Resubmitted {assignment.title}",
            previous_value=previous_value,
            new_value={"attempt_number": submission.attempt_number},
        )

    return submission


def grade_assignment_submission(
    *,
    submission: "AssignmentSubmission",
    marks_obtained: Decimal,
    feedback: str,
    graded_by: Staff,
    request: HttpRequest | None = None,
):
    """Spec §10 'Mark assignments' / 'Provide feedback' (Teacher Dashboard,
    §9)."""
    from django.utils import timezone

    from .models import AssignmentSubmission

    if marks_obtained > submission.assignment.max_marks:
        raise ValueError(
            f"marks_obtained ({marks_obtained}) cannot exceed "
            f"the assignment's max_marks ({submission.assignment.max_marks})."
        )

    submission.marks_obtained = marks_obtained
    submission.feedback = feedback
    submission.graded_by = graded_by
    submission.graded_at = timezone.now()
    submission.status = AssignmentSubmission.Status.GRADED
    submission.save()

    log_audit(
        actor=graded_by.user, action=AuditLog.Action.OTHER, request=request,
        target_model="AssignmentSubmission", target_object_id=submission.pk,
        description=f"Graded submission for {submission.assignment.title}",
        new_value={"marks_obtained": str(marks_obtained)},
    )
    return submission


def submit_quiz_attempt(
    *,
    attempt: "QuizAttempt",
    answers: dict[int, dict],
) -> "QuizAttempt":
    """Spec §10 'Implement automatic marking where appropriate'.

    `answers` maps question_id -> {"option_ids": [...]} for
    MULTIPLE_CHOICE/TRUE_FALSE/MULTIPLE_ANSWER, or {"text": "..."} for
    SHORT_ANSWER.

    Auto-grades objective question types by exact-match: a MULTIPLE_CHOICE
    or TRUE_FALSE question is correct if the single selected option is the
    correct one; a MULTIPLE_ANSWER question is correct only if the
    selected set exactly equals the correct set (no partial credit in
    this MVP — see docstring note below for extending to partial credit).
    SHORT_ANSWER questions are recorded but left ungraded
    (marks_awarded=None) for manual grading.
    """
    from django.utils import timezone

    from .models import QuizAnswer

    auto_score = Decimal("0")
    has_ungraded_manual = False

    for question in attempt.quiz.questions.all():
        payload = answers.get(question.pk, {})
        answer = QuizAnswer.objects.create(attempt=attempt, question=question)

        if question.question_type == question.QuestionType.SHORT_ANSWER:
            answer.text_answer = payload.get("text", "")
            answer.marks_awarded = None
            answer.save()
            has_ungraded_manual = True
            continue

        option_ids = set(payload.get("option_ids", []))
        answer.selected_options.set(option_ids)

        correct_ids = set(
            question.options.filter(is_correct=True).values_list("pk", flat=True)
        )
        is_correct = option_ids == correct_ids
        answer.marks_awarded = question.marks if is_correct else Decimal("0")
        answer.save()
        auto_score += answer.marks_awarded

    attempt.auto_score = auto_score
    attempt.submitted_at = timezone.now()
    attempt.is_fully_graded = not has_ungraded_manual
    attempt.save()
    return attempt


def grade_quiz_short_answer(
    *, answer: "QuizAnswer", marks_awarded: Decimal
) -> "QuizAnswer":
    """Manual grading step for SHORT_ANSWER questions within an attempt.
    Once every short-answer question in the attempt has been graded,
    the attempt is marked fully graded and its manual_score is totaled."""
    if marks_awarded > answer.question.marks:
        raise ValueError(
            f"marks_awarded ({marks_awarded}) cannot exceed the "
            f"question's marks ({answer.question.marks})."
        )

    answer.marks_awarded = marks_awarded
    answer.save()

    attempt = answer.attempt
    manual_questions = attempt.quiz.questions.filter(
        question_type="SHORT_ANSWER"
    )
    manual_answers = attempt.answers.filter(question__in=manual_questions)

    if not manual_answers.filter(marks_awarded__isnull=True).exists():
        attempt.manual_score = sum(
            (a.marks_awarded or Decimal("0")) for a in manual_answers
        )
        attempt.is_fully_graded = True
        attempt.save()

    return answer