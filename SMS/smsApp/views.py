# Absolute path: SMS/smsApp/views.py
import datetime
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .models import (
    AcademicYear,
    Announcement,
    Assessment,
    AssessmentMark,
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    Class,
    ClassSubject,
    CourseMaterial,
    Department,
    Discussion,
    Enrollment,
    FeeConcession,
    FeeStructure,
    Guardian,
    Invoice,
    LeaveRequest,
    Notification,
    Payment,
    Program,
    Quiz,
    QuizAttempt,
    Refund,
    ReportCard,
    ReportTemplate,
    Staff,
    StaffAttendanceRecord,
    StaffQualification,
    Stream,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
    TimetableSlot,
    Transcript,
    User,
)
from .permissions import RoleRequiredMixin
from .services import (
    apply_financial_adjustment,
    change_student_status,
    compute_school_academic_summary,
    compute_school_financial_summary,
    compute_staff_workload,
    compute_student_account_summary,
    compute_weighted_average,
    correct_attendance_record,
    deactivate_staff,
    decide_leave_request,
    decide_refund,
    generate_batch_reports,
    generate_invoice_for_student,
    generate_report_pdf,
    generate_transcript,
    get_children_for_guardian,
    get_dashboard_url_for_role,
    get_grade_for_mark,
    mark_attendance,
    mark_notification_read,
    reactivate_staff,
    record_assessment_marks,
    record_login,
    record_payment,
    record_staff_attendance,
    register_student,
    render_report_html,
    submit_assignment,
    submit_leave_request,
    transition_assessment_workflow,
    verify_transcript,
)
from .validators import validate_upload, validate_course_material_content, validate_document_content


class LoginView(DjangoLoginView):
    """Wraps Django's built-in LoginView to also write LoginHistory/AuditLog
    entries (spec §5 'View login history', §27 audit logging)."""

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        record_login(user=form.get_user(), request=self.request, was_successful=True)
        return response

    def form_invalid(self, form):
        # Only log a failed attempt if a real user was targeted, to avoid
        # creating noise/PII rows for arbitrary junk usernames.
        username = form.data.get("username")
        if username:
            user = User.objects.filter(username=username).first()
            if user:
                record_login(user=user, request=self.request, was_successful=False)
        return super().form_invalid(form)


class DashboardRouterView(LoginRequiredMixin, RedirectView):
    """Post-login landing point. Redirects each user to the dashboard that
    matches their role (spec §4 System User Types) instead of one shared
    dashboard, per the multi-dashboard structure §5-§19 describe.
    LoginRequiredMixin sends anonymous visitors to LOGIN_URL first —
    without it, `request.user` is an AnonymousUser with no `.role`."""

    permanent = False
    login_url = "dashboard:login"

    def get_redirect_url(self, *args, **kwargs):
        return get_dashboard_url_for_role(self.request.user)


class ComingSoonView(TemplateView):
    """Placeholder landing page for roles whose dedicated dashboard hasn't
    been built yet (wired up incrementally as Phases 6-19 land)."""

    template_name = "dashboard/coming_soon.html"


class AccountLockedView(TemplateView):
    template_name = "dashboard/account_locked.html"


class SuperAdminDashboardView(RoleRequiredMixin, TemplateView):
    """Spec §5 Super Admin Dashboard — top-level stat cards. Detailed
    sub-pages (user management, school configuration, audit log browser)
    are separate views added as those workflows are built out."""

    template_name = "dashboard/super_admin.html"
    allowed_roles = [User.Role.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        current_year = AcademicYear.objects.filter(is_current=True).first()
        current_term = Term.objects.filter(is_current=True).first()

        context.update(
            {
                "total_students": Student.objects.filter(is_active=True).count(),
                "total_staff": Staff.objects.filter(is_active=True).count(),
                "active_classes": Class.objects.filter(is_active=True).count(),
                "current_academic_year": current_year,
                "current_term": current_term,
                # Fees collected/outstanding populate once the Finance module
                # (Phase 19) exists; shown as "—" in the template until then.
            }
        )
        return context


class ReportCardHTMLView(RoleRequiredMixin, View):
    """Spec §15 'HTML report' / 'Printable report' — renders the report in
    the browser; the person prints via the browser's own print dialog
    (Ctrl/Cmd+P), so no separate 'printable' code path is needed.

    Spec §17 lets students view their own report books; spec §18 lets
    parents view their children's. STUDENT/PARENT are allowed here, but
    only for their own (or their own child's) ReportCard — the ownership
    check below is what actually enforces that, not just the role."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER, User.Role.STAFF_ADMIN,
        User.Role.STUDENT, User.Role.PARENT,
    ]

    def get(self, request, report_card_id):
        report_card = get_object_or_404(ReportCard, pk=report_card_id)
        if request.user.role == User.Role.STUDENT and report_card.student.user_id != request.user.pk:
            raise Http404("Report card not found.")
        if request.user.role == User.Role.PARENT:
            children = get_children_for_guardian(guardian_user=request.user)
            if not children.filter(pk=report_card.student_id).exists():
                raise Http404("Report card not found.")
        html = render_report_html(report_card=report_card)
        return HttpResponse(html)


class ReportCardPDFView(RoleRequiredMixin, View):
    """Spec §15 'PDF report' / 'Downloadable report'. Generates on first
    request if no PDF exists yet, then serves the stored file — repeat
    downloads don't re-render unless explicitly regenerated.

    Same student/parent ownership rule as ReportCardHTMLView above."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER, User.Role.STAFF_ADMIN,
        User.Role.STUDENT, User.Role.PARENT,
    ]

    def get(self, request, report_card_id):
        report_card = get_object_or_404(ReportCard, pk=report_card_id)
        if request.user.role == User.Role.STUDENT and report_card.student.user_id != request.user.pk:
            raise Http404("Report card not found.")
        if request.user.role == User.Role.PARENT:
            children = get_children_for_guardian(guardian_user=request.user)
            if not children.filter(pk=report_card.student_id).exists():
                raise Http404("Report card not found.")
        if not report_card.pdf_file:
            generate_report_pdf(report_card=report_card, generated_by=request.user, request=request)
            report_card.refresh_from_db()

        response = HttpResponse(report_card.pdf_file.read(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="report_{report_card.student.admission_number}.pdf"'
        )
        return response


class BatchReportGenerateView(RoleRequiredMixin, View):
    """Spec §15 'Batch reports'. POST-only action endpoint — the
    class/term/template picker UI lands with the Academic Admin dashboard
    build-out; this is the working generation endpoint it will call."""

    allowed_roles = [User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN]

    def post(self, request):
        class_group = get_object_or_404(Class, pk=request.POST.get("class_id"))
        term = get_object_or_404(Term, pk=request.POST.get("term_id"))
        template = get_object_or_404(ReportTemplate, pk=request.POST.get("template_id"))

        cards = generate_batch_reports(
            class_group=class_group, term=term, template=template,
            generated_by=request.user, request=request,
        )
        return HttpResponse(
            f"Generated {len(cards)} report(s) for {class_group} - {term}.",
            content_type="text/plain",
        )


class TranscriptGenerateAndDownloadView(RoleRequiredMixin, View):
    """Spec §16 'secure PDF documents'. Always generates a fresh transcript
    on request rather than serving a cached one — cumulative academic
    records must reflect every currently-published result, and each
    generation gets its own verification_code (spec §16 interpretation,
    see models.Transcript docstring).

    Spec §17 lets students view their own transcript — STUDENT is allowed
    here, but only for their own record."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN, User.Role.EXAM_OFFICER,
        User.Role.STUDENT,
    ]

    def get(self, request, student_id):
        student = get_object_or_404(Student, pk=student_id)
        if request.user.role == User.Role.STUDENT and student.user_id != request.user.pk:
            raise Http404("Student not found.")
        transcript = generate_transcript(
            student=student, generated_by=request.user, request=request
        )
        response = HttpResponse(transcript.pdf_file.read(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="transcript_{student.admission_number}.pdf"'
        )
        return response


class TranscriptVerifyView(View):
    """Public endpoint — spec §16's 'secure PDF documents' implies a third
    party (employer, other institution) should be able to confirm a
    transcript is genuine using only the code printed on it. Deliberately
    unauthenticated and deliberately minimal in what it discloses (no
    full mark list) to avoid leaking academic records to link-guessers."""

    def get(self, request, verification_code):
        result = verify_transcript(verification_code)
        if not result["valid"]:
            return HttpResponse("Invalid or unrecognized verification code.", status=404)
        lines = [
            "Transcript verified.",
            f"Student: {result['student_name']} ({result['admission_number']})",
            f"Issued: {result['generated_at']:%Y-%m-%d}",
            f"CGPA: {result['cgpa'] if result['cgpa'] is not None else '—'}",
            f"Status: {result['graduation_status']}",
        ]
        return HttpResponse("\n".join(lines), content_type="text/plain")


# =============================================================================
# Phase 16 — Student Dashboard (spec §17)
#
# "Students cannot modify official academic or financial records" is
# enforced structurally, not just documented: every view below is
# read-only except the two explicit, legitimate student actions the spec
# itself describes — submitting an assignment (LMS submission is not the
# same as editing a grade) and marking one's own notification read. No
# view here writes to Assessment/AssessmentMark/SubjectResult/Invoice/
# Payment/Attendance.
# =============================================================================

class StudentRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.STUDENT]
    active_nav = None  # set per-view; drives sidebar active-link highlighting

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_student(self, request) -> Student:
        return get_object_or_404(Student, user=request.user)

    def get_current_term(self, student: Student) -> Term | None:
        return Term.objects.filter(
            academic_year__school=student.school, is_current=True
        ).first()


class StudentDashboardView(StudentRequiredMixin, TemplateView):
    """Overview landing page — one stat card per spec §17 category
    (Academic, LMS, Finance, Communication) with links to the four
    detail pages below."""

    template_name = "dashboard/student/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)
        term = self.get_current_term(student)

        average = None
        if term is not None:
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=student, enrollments__academic_year=term.academic_year
            ).distinct()
            # Only include subjects that actually have at least one graded
            # component (weight_covered > 0) — compute_weighted_average()
            # returns Decimal("0"), never None, for an ungraded subject, so
            # including every enrolled subject unconditionally would count
            # not-yet-assessed subjects as a real 0% and skew the average
            # down. This mirrors the same gate assemble_report_data()
            # (Phase 9) uses for exactly this reason.
            totals = []
            for cs in class_subjects:
                summary = compute_weighted_average(student, cs, term)
                if summary["weight_covered"] > 0:
                    totals.append(summary["weighted_total"])
            if totals:
                average = sum(totals) / len(totals)

        pending_assignments = Assignment.objects.filter(
            class_subject__enrollments__student=student, is_published=True,
        ).exclude(
            submissions__student=student
        ).distinct().count()

        account_summary = compute_student_account_summary(student=student)
        unread_notifications = Notification.objects.filter(
            recipient=self.request.user, is_read=False
        ).count()

        context.update({
            "student": student,
            "current_term": term,
            "average": average,
            "pending_assignments": pending_assignments,
            "account_summary": account_summary,
            "unread_notifications": unread_notifications,
        })
        return context


class StudentAcademicView(StudentRequiredMixin, TemplateView):
    """Spec §17 Academic section: subjects, classes, results, grades,
    attendance. Report books/transcript/GPA/CGPA are separate downloadable
    documents (Phases 9-10) — this page links out to those rather than
    re-rendering their content inline."""

    template_name = "dashboard/student/academic.html"
    active_nav = "academic"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)
        term = self.get_current_term(student)

        subject_rows = []
        if term is not None:
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=student, enrollments__academic_year=term.academic_year
            ).distinct().select_related("subject")
            grading_scheme = student.school.grading_schemes.filter(is_default=True).first()
            for cs in class_subjects:
                summary = compute_weighted_average(student, cs, term)
                band = None
                if grading_scheme and summary["weight_covered"] > 0:
                    band = get_grade_for_mark(grading_scheme, summary["weighted_total"])
                subject_rows.append({
                    "subject": cs.subject.name,
                    "score": summary["weighted_total"],
                    "grade": band.grade if band else "-",
                    "is_complete": summary["is_complete"],
                })

        report_cards = ReportCard.objects.filter(student=student).select_related("term")

        context.update({
            "student": student,
            "current_term": term,
            "subject_rows": subject_rows,
            "report_cards": report_cards,
        })
        return context


class StudentLMSView(StudentRequiredMixin, TemplateView):
    """Spec §17 LMS section: course materials, assignments, quizzes,
    submission history, feedback."""

    template_name = "dashboard/student/lms.html"
    active_nav = "lms"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)
        term = self.get_current_term(student)

        class_subjects = ClassSubject.objects.filter(
            enrollments__student=student,
            enrollments__academic_year=term.academic_year if term else None,
        ).distinct() if term else ClassSubject.objects.none()

        materials = CourseMaterial.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject") if term else CourseMaterial.objects.none()

        assignments = Assignment.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject") if term else Assignment.objects.none()

        my_submissions = {
            s.assignment_id: s
            for s in AssignmentSubmission.objects.filter(student=student)
        }
        assignment_rows = [
            {"assignment": a, "submission": my_submissions.get(a.pk)} for a in assignments
        ]

        quizzes = Quiz.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject") if term else Quiz.objects.none()

        my_attempts = {
            a.quiz_id: a
            for a in QuizAttempt.objects.filter(student=student).order_by("-attempt_number")
        }
        quiz_rows = [
            {"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes
        ]

        context.update({
            "student": student,
            "current_term": term,
            "materials": materials,
            "assignment_rows": assignment_rows,
            "quiz_rows": quiz_rows,
        })
        return context


class StudentSubmitAssignmentView(StudentRequiredMixin, View):
    """The one write action on this page — spec §17 explicitly lists
    'Upload submissions' as something students do. This is not modifying
    an official record; grading (a separate, staff-only action) is what
    produces the official mark."""

    def post(self, request, assignment_id):
        student = self.get_student(request)
        assignment = get_object_or_404(Assignment, pk=assignment_id, is_published=True)

        try:
            submitted_file = validate_upload(
                request.FILES.get("submitted_file"),
                validate_document_content,
                size_limit_mb=25,
            )
            submit_assignment(
                assignment=assignment, student=student,
                submitted_file=submitted_file,
                submitted_text=request.POST.get("submitted_text", ""),
                request=request,
            )
        except (ValueError, ValidationError) as exc:
            return HttpResponseForbidden(str(exc))

        return redirect("dashboard:student_lms")


class StudentFinanceView(StudentRequiredMixin, TemplateView):
    """Spec §17 Finance section: fees, balance, invoices, receipts,
    payment history. This is the student's own account — showing full
    detail here is showing someone their own data, not a policy
    violation; §19/§23's 'do not expose financial info to unrelated
    staff' is a different boundary from 'a student sees their own bill'."""

    template_name = "dashboard/student/finance.html"
    active_nav = "finance"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)

        invoices = Invoice.objects.filter(student=student).order_by("-issue_date")
        payments = Payment.objects.filter(invoice__student=student).select_related(
            "invoice", "receipt"
        ).order_by("-payment_date")
        account_summary = compute_student_account_summary(student=student)

        context.update({
            "student": student,
            "invoices": invoices,
            "payments": payments,
            "account_summary": account_summary,
        })
        return context


class StudentCommunicationView(StudentRequiredMixin, TemplateView):
    """Spec §17 Communication section: announcements, notifications.
    'Messages' (private staff<->student messaging) has no backing model
    yet — Discussion/DiscussionReply (Phase 11) is course-level, not a
    general inbox. Flagged rather than fabricated; a dedicated messaging
    model is a reasonable follow-up phase."""

    template_name = "dashboard/student/communication.html"
    active_nav = "communication"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)

        announcements = Announcement.objects.filter(
            school=student.school, is_published=True,
        ).filter(
            models_q_student_audience()
        ).order_by("-created_at")[:20]

        notifications = Notification.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:50]

        context.update({
            "student": student,
            "announcements": announcements,
            "notifications": notifications,
        })
        return context


def models_q_student_audience():
    from django.db.models import Q

    return Q(audience=Announcement.Audience.ALL) | Q(audience=Announcement.Audience.STUDENTS)


class StudentMarkNotificationReadView(StudentRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification, pk=notification_id, recipient=request.user
        )
        mark_notification_read(notification=notification)
        return redirect("dashboard:student_communication")


# =============================================================================
# Phase 17 — Parent/Guardian Portal (spec §18). Same self-service,
# read-mostly shape as the Student Dashboard (Phase 16): every view here
# derives the parent's children from request.user via
# services.get_children_for_guardian() — never from a URL parameter — and
# every per-child page re-checks that the requested child actually
# belongs to this parent before showing anything. "Parents must not
# modify official academic records" (spec) — no view here writes to
# Assessment/AssessmentMark/Attendance/Invoice/Payment.
# =============================================================================

class ParentRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.PARENT]
    active_nav = None  # set per-view; drives sidebar active-link highlighting

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_children(self, request):
        return get_children_for_guardian(guardian_user=request.user)

    def get_child_or_404(self, request, student_id):
        """The ownership check that matters: a parent may only view a
        child actually linked to their own Guardian record."""
        children = self.get_children(request)
        return get_object_or_404(children, pk=student_id)


class ParentDashboardView(ParentRequiredMixin, TemplateView):
    """Overview landing page — one row per child (spec §18 'Parent ->
    Child 1/2/3'), each with a quick academic/finance snapshot and a link
    into that child's detail pages."""

    template_name = "dashboard/parent/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        children = self.get_children(self.request)

        child_rows = []
        for child in children:
            term = Term.objects.filter(
                academic_year__school=child.school, is_current=True
            ).first()
            average = None
            if term is not None:
                class_subjects = ClassSubject.objects.filter(
                    enrollments__student=child, enrollments__academic_year=term.academic_year
                ).distinct()
                totals = []
                for cs in class_subjects:
                    summary = compute_weighted_average(child, cs, term)
                    if summary["weight_covered"] > 0:
                        totals.append(summary["weighted_total"])
                if totals:
                    average = sum(totals) / len(totals)
            account_summary = compute_student_account_summary(student=child)
            child_rows.append({
                "student": child, "current_term": term, "average": average,
                "account_summary": account_summary,
            })

        unread_notifications = Notification.objects.filter(
            recipient=self.request.user, is_read=False
        ).count()

        context.update({
            "child_rows": child_rows,
            "unread_notifications": unread_notifications,
        })
        return context


class ParentChildAcademicView(ParentRequiredMixin, TemplateView):
    """Spec §18: academic performance, report books, attendance,
    assignments — for one specific child."""

    template_name = "dashboard/parent/child_academic.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        child = self.get_child_or_404(self.request, kwargs["student_id"])
        term = Term.objects.filter(
            academic_year__school=child.school, is_current=True
        ).first()

        subject_rows = []
        assignment_rows = []
        if term is not None:
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=child, enrollments__academic_year=term.academic_year
            ).distinct().select_related("subject")
            grading_scheme = child.school.grading_schemes.filter(is_default=True).first()
            for cs in class_subjects:
                summary = compute_weighted_average(child, cs, term)
                band = None
                if grading_scheme and summary["weight_covered"] > 0:
                    band = get_grade_for_mark(grading_scheme, summary["weighted_total"])
                subject_rows.append({
                    "subject": cs.subject.name,
                    "score": summary["weighted_total"],
                    "grade": band.grade if band else "-",
                    "is_complete": summary["is_complete"],
                })

            assignments = Assignment.objects.filter(
                class_subject__in=class_subjects, is_published=True,
            ).select_related("class_subject__subject")
            my_submissions = {
                s.assignment_id: s
                for s in AssignmentSubmission.objects.filter(student=child)
            }
            assignment_rows = [
                {"assignment": a, "submission": my_submissions.get(a.pk)} for a in assignments
            ]

        report_cards = ReportCard.objects.filter(student=child).select_related("term")

        context.update({
            "student": child,
            "current_term": term,
            "subject_rows": subject_rows,
            "assignment_rows": assignment_rows,
            "report_cards": report_cards,
        })
        return context


class ParentChildFinanceView(ParentRequiredMixin, TemplateView):
    """Spec §18: fees, payments, balances — for one specific child.
    Same 'own child's data' reasoning as the Student Finance page
    (Phase 16): this is showing a guardian their own dependent's
    billing, not a cross-account leak."""

    template_name = "dashboard/parent/child_finance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        child = self.get_child_or_404(self.request, kwargs["student_id"])

        invoices = Invoice.objects.filter(student=child).order_by("-issue_date")
        payments = Payment.objects.filter(invoice__student=child).select_related(
            "invoice", "receipt"
        ).order_by("-payment_date")
        account_summary = compute_student_account_summary(student=child)

        context.update({
            "student": child,
            "invoices": invoices,
            "payments": payments,
            "account_summary": account_summary,
        })
        return context


class ParentCommunicationView(ParentRequiredMixin, TemplateView):
    """Spec §18: announcements, and (spec §22) parent-facing
    notifications like 'Your child's Term 2 report is available.'
    Not scoped to a single child — a parent's notification inbox and the
    school's PARENTS-audience announcements are shared across all their
    children."""

    template_name = "dashboard/parent/communication.html"
    active_nav = "communication"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        children = self.get_children(self.request)
        schools = {child.school_id for child in children}

        announcements = Announcement.objects.filter(
            school_id__in=schools, is_published=True,
        ).filter(
            _parent_audience_q()
        ).order_by("-created_at")[:20]

        notifications = Notification.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:50]

        context.update({
            "children": children,
            "announcements": announcements,
            "notifications": notifications,
        })
        return context


def _parent_audience_q():
    from django.db.models import Q
    return Q(audience=Announcement.Audience.ALL) | Q(audience=Announcement.Audience.PARENTS)


class ParentMarkNotificationReadView(ParentRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification, pk=notification_id, recipient=request.user
        )
        mark_notification_read(notification=notification)
        return redirect("dashboard:parent_communication")


# =============================================================================
# Phase 18 — Teacher Dashboard (spec §9)
#
# Ownership boundary: every class/subject/assessment-scoped action checks
# TeachingAssignment.objects.filter(teacher=staff, class_subject=...,
# is_active=True) before allowing access — a teacher must never be able to
# manage a class or grade an assessment they aren't actually assigned to,
# even by guessing a URL. Spec §9 "Teachers must not be able to approve
# their own final results" is enforced by transition_assessment_workflow()
# itself (Phase 8), reused here rather than re-implemented.
# =============================================================================

class TeacherRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.TEACHER, User.Role.CLASS_TEACHER]
    active_nav = None  # set per-view; drives sidebar active-link highlighting

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_staff(self, request) -> Staff:
        return get_object_or_404(Staff, user=request.user)

    def get_my_class_subjects(self, staff):
        return ClassSubject.objects.filter(
            teaching_assignments__teacher=staff, teaching_assignments__is_active=True,
        ).distinct().select_related("subject", "class_group")

    def get_owned_class_subject_or_404(self, staff, class_subject_id):
        return get_object_or_404(self.get_my_class_subjects(staff), pk=class_subject_id)


class TeacherDashboardView(TeacherRequiredMixin, TemplateView):
    """Overview landing page — spec §9's category list condensed into
    stat cards linking to the detail pages below."""

    template_name = "dashboard/teacher/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)

        pending_grading = AssignmentSubmission.objects.filter(
            assignment__class_subject__in=class_subjects,
            status=AssignmentSubmission.Status.SUBMITTED,
        ).count()

        from django.utils import timezone

        from .models import TimetableSlot as TimetableSlotModel

        # Bug fix: this widget is meant to be "what am I teaching today"
        # (the full week is already available via TeacherTimetableView),
        # but was previously missing a day_of_week filter entirely and
        # silently showed the teacher's whole week here instead.
        weekday_map = {
            0: TimetableSlotModel.DayOfWeek.MONDAY, 1: TimetableSlotModel.DayOfWeek.TUESDAY,
            2: TimetableSlotModel.DayOfWeek.WEDNESDAY, 3: TimetableSlotModel.DayOfWeek.THURSDAY,
            4: TimetableSlotModel.DayOfWeek.FRIDAY, 5: TimetableSlotModel.DayOfWeek.SATURDAY,
        }
        today_code = weekday_map.get(timezone.localtime(timezone.now()).weekday())
        today_slots = TimetableSlot.objects.filter(
            teacher=staff, day_of_week=today_code
        ).select_related("period", "class_group", "room").order_by("period__order") if today_code else TimetableSlot.objects.none()

        unread_notifications = Notification.objects.filter(
            recipient=self.request.user, is_read=False
        ).count()

        context.update({
            "staff": staff,
            "class_subject_count": class_subjects.count(),
            "pending_grading": pending_grading,
            "today_slots": today_slots,
            "unread_notifications": unread_notifications,
        })
        return context


class TeacherClassesView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'My classes', 'My subjects', 'My students'."""

    template_name = "dashboard/teacher/classes.html"
    active_nav = "classes"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)

        rows = []
        for cs in class_subjects:
            student_count = Enrollment.objects.filter(class_subject=cs).count()
            rows.append({"class_subject": cs, "student_count": student_count})

        context.update({"staff": staff, "rows": rows})
        return context


class TeacherClassRosterView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'My students' — the enrolled roster for one owned class+subject."""

    template_name = "dashboard/teacher/roster.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subject = self.get_owned_class_subject_or_404(staff, kwargs["class_subject_id"])

        students = Student.objects.filter(
            enrollments__class_subject=class_subject
        ).distinct().order_by("admission_number")

        context.update({"class_subject": class_subject, "students": students})
        return context


class TeacherTimetableView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'My timetable'."""

    template_name = "dashboard/teacher/timetable.html"
    active_nav = "timetable"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        slots = TimetableSlot.objects.filter(teacher=staff).select_related(
            "period", "class_group", "room", "teaching_assignment__class_subject__subject"
        ).order_by("day_of_week", "period__order")
        context.update({"staff": staff, "slots": slots})
        return context


class TeacherAttendanceView(TeacherRequiredMixin, TemplateView):
    """Spec §9/§11 'Record attendance' — the teacher's initial marking
    view for one owned class+subject on a given date (defaults today)."""

    template_name = "dashboard/teacher/attendance.html"

    def get_context_data(self, **kwargs):
        from django.utils import timezone

        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subject = self.get_owned_class_subject_or_404(staff, kwargs["class_subject_id"])
        date_str = self.request.GET.get("date")
        target_date = (
            datetime.date.fromisoformat(date_str) if date_str
            else timezone.localtime(timezone.now()).date()
        )

        students = Student.objects.filter(
            enrollments__class_subject=class_subject
        ).distinct().order_by("admission_number")

        existing = {}
        session = AttendanceSession.objects.filter(
            class_subject=class_subject, date=target_date
        ).first()
        if session:
            existing = {r.student_id: r for r in session.records.all()}

        student_rows = [
            {"student": s, "record": existing.get(s.pk)} for s in students
        ]

        context.update({
            "class_subject": class_subject, "students": students,
            "target_date": target_date, "existing": existing,
            "student_rows": student_rows,
            "session_locked": session.is_locked if session else False,
            "status_choices": AttendanceRecord.Status.choices,
        })
        return context

    def post(self, request, class_subject_id):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(staff, class_subject_id)
        target_date = datetime.date.fromisoformat(request.POST.get("date"))

        term = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).select_related("term").first()
        term = term.term if term else None

        records = {}
        for student in Student.objects.filter(enrollments__class_subject=class_subject).distinct():
            status = request.POST.get(f"status_{student.pk}")
            if status:
                records[student.pk] = {
                    "status": status, "notes": request.POST.get(f"notes_{student.pk}", ""),
                }

        try:
            mark_attendance(
                class_subject=class_subject, term=term, date=target_date,
                taken_by=staff, records=records, request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))

        return redirect(
            f"{reverse('dashboard:teacher_attendance', args=[class_subject.pk])}?date={target_date}"
        )


class TeacherAssignmentsView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Assignments' — list across all owned classes, plus the
    create form ('Create assignment', 'Set instructions', 'Set deadline',
    'Define marks', 'Define submission format')."""

    template_name = "dashboard/teacher/assignments.html"
    active_nav = "assignments"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)
        assignments = Assignment.objects.filter(
            class_subject__in=class_subjects
        ).select_related("class_subject__subject").order_by("-deadline")

        context.update({
            "class_subjects": class_subjects, "assignments": assignments,
            "submission_formats": Assignment.SubmissionFormat.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(
            staff, request.POST.get("class_subject_id")
        )
        term_id = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).values_list("term_id", flat=True).first()

        Assignment.objects.create(
            class_subject=class_subject, term_id=term_id,
            title=request.POST.get("title", ""),
            instructions=request.POST.get("instructions", ""),
            deadline=request.POST.get("deadline"),
            max_marks=request.POST.get("max_marks") or Decimal("100"),
            submission_format=request.POST.get("submission_format", Assignment.SubmissionFormat.FILE_UPLOAD),
            allow_resubmission=bool(request.POST.get("allow_resubmission")),
            created_by=staff,
        )
        return redirect("dashboard:teacher_assignments")


class TeacherAssignmentSubmissionsView(TeacherRequiredMixin, View):
    """Spec §9 'Mark assignments', 'Provide feedback' — reuses
    services.grade_assignment_submission() (Phase 11), already tested."""

    template_name = "dashboard/teacher/submissions.html"

    def _get_owned_assignment(self, request, assignment_id):
        staff = self.get_staff(request)
        return get_object_or_404(
            Assignment, pk=assignment_id, class_subject__in=self.get_my_class_subjects(staff)
        )

    def get(self, request, assignment_id):
        assignment = self._get_owned_assignment(request, assignment_id)
        submissions = assignment.submissions.select_related("student").order_by("-submitted_at")
        return render(request, self.template_name, {
            "assignment": assignment, "submissions": submissions,
        })

    def post(self, request, assignment_id):
        from .services import grade_assignment_submission

        assignment = self._get_owned_assignment(request, assignment_id)
        staff = self.get_staff(request)
        submission = get_object_or_404(
            assignment.submissions, pk=request.POST.get("submission_id")
        )
        try:
            grade_assignment_submission(
                submission=submission,
                marks_obtained=Decimal(request.POST.get("marks_obtained")),
                feedback=request.POST.get("feedback", ""),
                graded_by=staff, request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:teacher_assignment_submissions", assignment_id=assignment.pk)


class TeacherMaterialsView(TeacherRequiredMixin, TemplateView):
    """Spec §9/§10 'Upload learning materials', 'Upload PDFs', 'Upload videos'."""

    template_name = "dashboard/teacher/materials.html"
    active_nav = "materials"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)
        materials = CourseMaterial.objects.filter(
            class_subject__in=class_subjects
        ).select_related("class_subject__subject").order_by("-created_at")

        context.update({
            "class_subjects": class_subjects, "materials": materials,
            "material_types": CourseMaterial.MaterialType.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(
            staff, request.POST.get("class_subject_id")
        )
        term_id = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).values_list("term_id", flat=True).first()

        # Size limit varies by material type: images are smaller, videos/presentations larger
        material_type = request.POST.get("material_type")
        size_limit_mb = {
            CourseMaterial.MaterialType.IMAGE: 5,
            CourseMaterial.MaterialType.PDF: 200,
            CourseMaterial.MaterialType.DOCUMENT: 200,
            CourseMaterial.MaterialType.VIDEO: 200,
            CourseMaterial.MaterialType.PRESENTATION: 200,
        }.get(material_type, 200)

        try:
            file_obj = validate_upload(
                request.FILES.get("file"),
                validate_course_material_content,
                size_limit_mb=size_limit_mb,
            )
        except ValidationError as exc:
            return HttpResponseForbidden(str(exc))

        CourseMaterial.objects.create(
            class_subject=class_subject, term_id=term_id,
            material_type=material_type,
            title=request.POST.get("title", ""),
            description=request.POST.get("description", ""),
            file=file_obj,
            external_url=request.POST.get("external_url", ""),
            text_content=request.POST.get("text_content", ""),
            uploaded_by=staff,
        )
        return redirect("dashboard:teacher_materials")


class TeacherAnnouncementsView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Publish course announcements' — this is Discussion
    (course-scoped, Phase 11), distinct from the school-wide Announcement
    model (Phase 15). See Phase 11's design note on that split."""

    template_name = "dashboard/teacher/announcements.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subject = self.get_owned_class_subject_or_404(staff, kwargs["class_subject_id"])
        discussions = Discussion.objects.filter(
            class_subject=class_subject
        ).order_by("-is_pinned", "-created_at")

        context.update({"class_subject": class_subject, "discussions": discussions})
        return context

    def post(self, request, class_subject_id):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(staff, class_subject_id)
        term_id = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).values_list("term_id", flat=True).first()

        Discussion.objects.create(
            class_subject=class_subject, term_id=term_id,
            thread_type=Discussion.ThreadType.ANNOUNCEMENT,
            title=request.POST.get("title", ""), body=request.POST.get("body", ""),
            created_by=request.user,
        )
        return redirect("dashboard:teacher_announcements", class_subject_id=class_subject.pk)


class TeacherAssessmentsView(TeacherRequiredMixin, TemplateView):
    """Spec §9/§12 'Create assessments', 'Create exams'. Creates an
    Assessment against an AssessmentComponent that Academic Admin has
    already configured (Phase 7) — teachers don't define weighting
    schemes, only schedule instances of them."""

    template_name = "dashboard/teacher/assessments.html"
    active_nav = "assessments"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)
        assessments = Assessment.objects.filter(
            class_subject__in=class_subjects
        ).select_related("class_subject__subject", "component").order_by("-created_at")

        context.update({"class_subjects": class_subjects, "assessments": assessments})
        return context


class TeacherMarksEntryView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Enter marks'. Reuses services.record_assessment_marks()
    (this phase) for the write, and services.transition_assessment_workflow()
    (Phase 8) for the 'submit for review' action — which itself enforces
    'teachers cannot approve their own results' if this same teacher later
    tries to also approve it."""

    template_name = "dashboard/teacher/marks_entry.html"

    def _get_owned_assessment(self, request, assessment_id):
        staff = self.get_staff(request)
        return get_object_or_404(
            Assessment, pk=assessment_id, class_subject__in=self.get_my_class_subjects(staff)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assessment = self._get_owned_assessment(self.request, kwargs["assessment_id"])
        students = Student.objects.filter(
            enrollments__class_subject=assessment.class_subject
        ).distinct().order_by("admission_number")
        existing_marks = {
            m.student_id: m for m in AssessmentMark.objects.filter(assessment=assessment)
        }
        student_rows = [
            {"student": s, "mark": existing_marks.get(s.pk)} for s in students
        ]
        context.update({
            "assessment": assessment, "students": students, "existing_marks": existing_marks,
            "student_rows": student_rows,
        })
        return context

    def post(self, request, assessment_id):
        assessment = self._get_owned_assessment(request, assessment_id)
        staff = self.get_staff(request)

        if request.POST.get("action") == "submit_for_review":
            try:
                transition_assessment_workflow(
                    assessment=assessment, to_status=Assessment.WorkflowStatus.SUBMITTED,
                    actor=request.user, request=request,
                )
            except ValueError as exc:
                return HttpResponseForbidden(str(exc))
            return redirect("dashboard:teacher_assessments")

        marks = {}
        for student in Student.objects.filter(enrollments__class_subject=assessment.class_subject).distinct():
            raw = request.POST.get(f"mark_{student.pk}")
            if raw not in (None, ""):
                marks[student.pk] = Decimal(raw)

        try:
            record_assessment_marks(
                assessment=assessment, teacher=staff, marks=marks, request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))

        return redirect("dashboard:teacher_marks_entry", assessment_id=assessment.pk)


class TeacherCommunicationView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Announcements', 'Notifications' — the teacher's own inbox
    (school-wide/staff-audience Announcements + personal Notifications),
    distinct from course-level announcements they publish themselves
    (TeacherAnnouncementsView, above)."""

    template_name = "dashboard/teacher/communication.html"
    active_nav = "communication"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)

        announcements = Announcement.objects.filter(
            school=staff.school, is_published=True,
        ).filter(_teacher_audience_q()).order_by("-created_at")[:20]

        notifications = Notification.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:50]

        context.update({"announcements": announcements, "notifications": notifications})
        return context


def _teacher_audience_q():
    from django.db.models import Q
    return (
        Q(audience=Announcement.Audience.ALL)
        | Q(audience=Announcement.Audience.TEACHERS)
        | Q(audience=Announcement.Audience.STAFF)
    )


class TeacherMarkNotificationReadView(TeacherRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification, pk=notification_id, recipient=request.user
        )
        mark_notification_read(notification=notification)
        return redirect("dashboard:teacher_communication")


# =============================================================================
# Phase 19 — Finance Admin Dashboard (spec §19, §23)
#
# Spec §23 'Financial and Academic Separation': Finance Admin gets fees,
# payments, invoices, financial reporting — never grades, assessments, or
# attendance. Every queryset here is scoped to Invoice/Payment/Refund and
# minimal student identity (name, admission number); nothing joins into
# AssessmentMark, SubjectResult, or AttendanceRecord.
# =============================================================================

class FinanceRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.FINANCE_ADMIN, User.Role.ACCOUNTANT]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, request):
        # Finance Admin/Accountant accounts aren't tied to a Student/Staff/
        # Guardian profile the way other roles are (Phase 1's User model
        # has no direct `school` FK) — for a single-school deployment this
        # resolves to the one School row; multi-school support would add
        # a FinanceAdmin-school assignment model as a follow-up.
        from .models import School
        return School.objects.first()


class FinanceAdminDashboardView(FinanceRequiredMixin, TemplateView):
    """Overview — spec §19 stat cards: total billed/collected/outstanding,
    arrears, overdue invoices, recent payments. No academic data anywhere
    on this page."""

    template_name = "dashboard/finance/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        summary = compute_school_financial_summary(school=school) if school else {}

        recent_payments = Payment.objects.filter(
            invoice__school=school
        ).select_related("invoice__student").order_by("-payment_date")[:10] if school else []

        pending_refunds = Refund.objects.filter(
            payment__invoice__school=school, status=Refund.Status.REQUESTED
        ).count() if school else 0

        context.update({
            "school": school, "summary": summary,
            "recent_payments": recent_payments, "pending_refunds": pending_refunds,
        })
        return context


class FinanceAdminInvoicesView(FinanceRequiredMixin, TemplateView):
    """Spec §19 'Invoices'. List + generate-invoice action, reusing
    services.generate_invoice_for_student() (Phase 12, already tested)."""

    template_name = "dashboard/finance/invoices.html"
    active_nav = "invoices"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        status_filter = self.request.GET.get("status", "")

        invoices = Invoice.objects.filter(school=school).select_related(
            "student__user", "academic_year", "term"
        ).order_by("-issue_date") if school else Invoice.objects.none()
        if status_filter:
            invoices = invoices.filter(status=status_filter)

        context.update({
            "school": school, "invoices": invoices,
            "status_choices": Invoice.Status.choices, "status_filter": status_filter,
            "fee_structures": FeeStructure.objects.filter(school=school, is_active=True) if school else [],
            "students": Student.objects.filter(school=school, is_active=True).select_related("user") if school else [],
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        student = get_object_or_404(Student, pk=request.POST.get("student_id"), school=school)
        fee_structure = get_object_or_404(
            FeeStructure, pk=request.POST.get("fee_structure_id"), school=school
        )
        try:
            generate_invoice_for_student(
                student=student, fee_structure=fee_structure,
                academic_year=fee_structure.academic_year, term=fee_structure.term,
                issued_by=request.user, due_date=request.POST.get("due_date"),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_invoices")


class FinanceAdminInvoiceDetailView(FinanceRequiredMixin, View):
    """Spec §19 'Payments', 'Partial payments', 'Balances'. View one
    invoice + record a payment against it, reusing
    services.record_payment() (Phase 12, already tested)."""

    template_name = "dashboard/finance/invoice_detail.html"
    active_nav = "invoices"

    def get(self, request, invoice_id):
        school = self.get_school(request)
        invoice = get_object_or_404(
            Invoice.objects.select_related("student__user"), pk=invoice_id, school=school
        )
        return render(request, self.template_name, {
            "invoice": invoice, "active": self.active_nav,
            "payment_methods": Payment.Method.choices,
        })

    def post(self, request, invoice_id):
        school = self.get_school(request)
        invoice = get_object_or_404(Invoice, pk=invoice_id, school=school)
        try:
            record_payment(
                invoice=invoice, amount=Decimal(request.POST.get("amount")),
                payment_method=request.POST.get("payment_method"),
                payment_date=request.POST.get("payment_date"),
                received_by=request.user, payer_name=request.POST.get("payer_name", ""),
                gateway_reference=request.POST.get("gateway_reference", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_invoice_detail", invoice_id=invoice.pk)


class FinanceAdminRefundsView(FinanceRequiredMixin, TemplateView):
    """Spec §19 'Refunds'. Approval queue, reusing
    services.decide_refund() (Phase 12, already tested — including the
    'cannot decide the same refund twice' guard)."""

    template_name = "dashboard/finance/refunds.html"
    active_nav = "refunds"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        refunds = Refund.objects.filter(
            payment__invoice__school=school
        ).select_related("payment__invoice__student__user").order_by("-requested_at") if school else []
        context.update({"school": school, "refunds": refunds})
        return context

    def post(self, request):
        school = self.get_school(request)
        refund = get_object_or_404(
            Refund, pk=request.POST.get("refund_id"), payment__invoice__school=school
        )
        approve = request.POST.get("action") == "approve"
        try:
            decide_refund(
                refund=refund, approve=approve, decided_by=request.user,
                refund_method=request.POST.get("refund_method", ""),
                reference_number=request.POST.get("reference_number", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_refunds")


# =============================================================================
# Phase 20 — Staff Admin Dashboard (spec §6)
#
# 'Restrict sensitive HR information to authorized users' — this entire
# section is gated to STAFF_ADMIN (and Super Admin via RoleRequiredMixin's
# is_superuser override). No other role's dashboard reads Staff HR fields
# beyond public-facing display name.
# =============================================================================

class StaffAdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.STAFF_ADMIN]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, request):
        from .models import School
        return School.objects.first()


class StaffAdminDashboardView(StaffAdminRequiredMixin, TemplateView):
    """Overview — staff counts by employment status, pending leave
    requests, today's attendance summary."""

    template_name = "dashboard/staff_admin/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        staff_qs = Staff.objects.filter(school=school) if school else Staff.objects.none()

        context.update({
            "school": school,
            "total_staff": staff_qs.filter(is_active=True).count(),
            "on_leave_count": staff_qs.filter(
                employment_status=Staff.EmploymentStatus.ON_LEAVE
            ).count(),
            "pending_leave_requests": LeaveRequest.objects.filter(
                staff__school=school, status=LeaveRequest.Status.PENDING
            ).count() if school else 0,
            "departments": Department.objects.filter(school=school, is_active=True) if school else [],
        })
        return context


class StaffAdminStaffListView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 'Staff profiles', 'Departments', 'Job titles', 'Employment
    status'. List + search; create-staff action is deliberately NOT a
    quick inline form here — creating a Staff record requires first
    creating its linked User (username/password/role), which is a
    distinct enough operation to warrant its own confirmation step rather
    than a silent side-effect of this list page. See StaffAdminStaffCreateView."""

    template_name = "dashboard/staff_admin/staff_list.html"
    active_nav = "staff"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        staff_qs = Staff.objects.filter(school=school).select_related(
            "user", "department"
        ).order_by("-created_at") if school else Staff.objects.none()

        search = self.request.GET.get("q", "").strip()
        if search:
            from django.db.models import Q
            staff_qs = staff_qs.filter(
                Q(staff_id__icontains=search) | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        context.update({
            "school": school, "staff_list": staff_qs, "search": search,
            "departments": Department.objects.filter(school=school, is_active=True) if school else [],
        })
        return context


class StaffAdminStaffCreateView(StaffAdminRequiredMixin, View):
    """Spec §6 'Create staff'. Creates the User (login) and Staff
    (HR profile) together — a Staff record cannot exist without a User,
    per the OneToOneField in Phase 3's model."""

    def post(self, request):
        from django.utils.crypto import get_random_string

        school = self.get_school(request)
        username = request.POST.get("username", "").strip()
        if not username:
            return HttpResponseForbidden("Username is required.")

        new_user = User.objects.create_user(
            username=username, password=request.POST.get("password") or get_random_string(16),
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            email=request.POST.get("email", ""),
            role=request.POST.get("role", User.Role.TEACHER),
        )
        Staff.objects.create(
            user=new_user, school=school, staff_id=request.POST.get("staff_id", ""),
            department_id=request.POST.get("department_id") or None,
            job_title=request.POST.get("job_title", ""),
            date_hired=request.POST.get("date_hired") or datetime.date.today(),
        )
        return redirect("dashboard:staff_admin_staff_list")


class StaffAdminStaffDetailView(StaffAdminRequiredMixin, View):
    """Spec §6 'Edit staff', 'Deactivate staff', 'Qualifications',
    'Certifications', 'Emergency contacts'."""

    template_name = "dashboard/staff_admin/staff_detail.html"
    active_nav = "staff"

    def get(self, request, staff_id):
        school = self.get_school(request)
        staff = get_object_or_404(Staff, pk=staff_id, school=school)
        return render(request, self.template_name, {
            "staff": staff, "active": self.active_nav,
            "qualifications": staff.qualifications.all(),
            "employment_statuses": Staff.EmploymentStatus.choices,
        })

    def post(self, request, staff_id):
        school = self.get_school(request)
        staff = get_object_or_404(Staff, pk=staff_id, school=school)
        action = request.POST.get("action")

        if action == "deactivate":
            deactivate_staff(
                staff=staff, deactivated_by=request.user,
                reason=request.POST.get("reason", ""), request=request,
            )
        elif action == "reactivate":
            reactivate_staff(staff=staff, reactivated_by=request.user, request=request)
        else:
            # Edit profile fields.
            staff.job_title = request.POST.get("job_title", staff.job_title)
            staff.emergency_contact_name = request.POST.get(
                "emergency_contact_name", staff.emergency_contact_name
            )
            staff.emergency_contact_phone = request.POST.get(
                "emergency_contact_phone", staff.emergency_contact_phone
            )
            staff.save()
        return redirect("dashboard:staff_admin_staff_detail", staff_id=staff.pk)


class StaffAdminAttendanceView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 'Staff attendance' — mark attendance for all staff on a
    given date."""

    template_name = "dashboard/staff_admin/attendance.html"
    active_nav = "attendance"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        target_date = self.request.GET.get("date") or datetime.date.today().isoformat()

        staff_qs = Staff.objects.filter(school=school, is_active=True).select_related("user") if school else Staff.objects.none()
        existing = {
            r.staff_id: r for r in StaffAttendanceRecord.objects.filter(
                staff__school=school, date=target_date
            )
        } if school else {}
        staff_rows = [{"staff": s, "record": existing.get(s.pk)} for s in staff_qs]

        context.update({
            "school": school, "staff_rows": staff_rows, "target_date": target_date,
            "status_choices": StaffAttendanceRecord.Status.choices,
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        target_date = request.POST.get("date")
        for staff in Staff.objects.filter(school=school, is_active=True):
            status = request.POST.get(f"status_{staff.pk}")
            if not status:
                continue
            record_staff_attendance(
                staff=staff, date=target_date, status=status, recorded_by=request.user,
                notes=request.POST.get(f"notes_{staff.pk}", ""), request=request,
            )
        return redirect(f"{reverse('dashboard:staff_admin_attendance')}?date={target_date}")


class StaffAdminLeaveRequestsView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 leave workflow — the Staff Admin review/approve/reject
    step. Approval/rejection reuses services.decide_leave_request(),
    which sends the required notification (Phase 15)."""

    template_name = "dashboard/staff_admin/leave_requests.html"
    active_nav = "leave"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        requests_qs = LeaveRequest.objects.filter(
            staff__school=school
        ).select_related("staff__user").order_by("-requested_at") if school else []
        context.update({"school": school, "leave_requests": requests_qs})
        return context

    def post(self, request):
        school = self.get_school(request)
        leave_request = get_object_or_404(
            LeaveRequest, pk=request.POST.get("leave_request_id"), staff__school=school
        )
        approve = request.POST.get("action") == "approve"
        try:
            decide_leave_request(
                leave_request=leave_request, approve=approve, decided_by=request.user,
                decision_notes=request.POST.get("decision_notes", ""), request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:staff_admin_leave_requests")


class StaffAdminWorkloadView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 'Staff workload': assigned classes, subjects, teaching
    hours, timetable. Read-only — reuses Phase 5/14 data via
    compute_staff_workload(), no new write path."""

    template_name = "dashboard/staff_admin/workload.html"
    active_nav = "workload"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        term = Term.objects.filter(academic_year__school=school, is_current=True).first() if school else None

        rows = []
        if term:
            for staff in Staff.objects.filter(school=school, is_active=True).select_related("user"):
                workload = compute_staff_workload(staff=staff, term=term)
                rows.append({"staff": staff, "workload": workload})

        context.update({"school": school, "term": term, "workload_rows": rows})
        return context


class MyLeaveRequestsView(LoginRequiredMixin, TemplateView):
    """Spec §6 leave workflow, step one: 'Staff submits leave request.'
    Generic self-service view for any staff member (Teacher, Librarian,
    Accountant, etc.) — not gated to a specific staff role, only to
    having a linked Staff profile at all, since every staff type can
    take leave. This is the missing entry point the workflow needs:
    without it, LeaveRequest rows could only ever be created via Django
    admin, never by the staff member themselves."""

    template_name = "dashboard/staff_self_service/my_leave_requests.html"

    def get_staff_or_404(self, request):
        return get_object_or_404(Staff, user=request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff_or_404(self.request)
        context.update({
            "staff": staff,
            "leave_requests": staff.leave_requests.order_by("-requested_at"),
            "leave_types": LeaveRequest.LeaveType.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff_or_404(request)
        try:
            submit_leave_request(
                staff=staff, leave_type=request.POST.get("leave_type"),
                start_date=request.POST.get("start_date"),
                end_date=request.POST.get("end_date"),
                reason=request.POST.get("reason", ""), request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:my_leave_requests")


# =============================================================================
# Phase 21 — Academic Admin Dashboard (spec §7)
#
# 'Academic Admin must manage academic operations without accessing
# confidential financial information' — no view in this section imports
# or queries Invoice/Payment/Refund/FeeStructure/FeeConcession.
#
# Two of these views close loops left open since earlier phases: result
# approval (Phase 8's workflow was fully built/tested but had no
# Academic Admin-facing view until now) and attendance correction
# (Phase 6's correct_attendance_record() was built/tested but likewise
# never had a UI entry point).
# =============================================================================

class AcademicAdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.ACADEMIC_ADMIN]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, request):
        from .models import School
        return School.objects.first()


class AcademicAdminDashboardView(AcademicAdminRequiredMixin, TemplateView):
    template_name = "dashboard/academic_admin/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        summary = compute_school_academic_summary(school=school) if school else {}
        context.update({"school": school, "summary": summary})
        return context


class AcademicAdminStudentsView(AcademicAdminRequiredMixin, TemplateView):
    """Spec §7 'Student profiles', 'Classes', 'Streams', 'Student
    status'. List + search + register-student action."""

    template_name = "dashboard/academic_admin/students.html"
    active_nav = "students"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        students_qs = Student.objects.filter(school=school).select_related(
            "user", "current_class", "current_stream"
        ).order_by("-created_at") if school else Student.objects.none()

        search = self.request.GET.get("q", "").strip()
        if search:
            from django.db.models import Q
            students_qs = students_qs.filter(
                Q(admission_number__icontains=search) | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        context.update({
            "school": school, "students": students_qs, "search": search,
            "classes": Class.objects.filter(school=school, is_active=True) if school else [],
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        try:
            register_student(
                school=school, username=request.POST.get("username", "").strip(),
                password=request.POST.get("password") or None,
                first_name=request.POST.get("first_name", ""),
                last_name=request.POST.get("last_name", ""),
                email=request.POST.get("email", ""),
                admission_number=request.POST.get("admission_number", ""),
                admission_date=request.POST.get("admission_date"),
                current_class=(
                    Class.objects.filter(pk=request.POST.get("current_class_id")).first()
                    if request.POST.get("current_class_id") else None
                ),
                registered_by=request.user, request=request,
            )
        except (ValueError, TypeError) as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:academic_admin_students")


class AcademicAdminStudentDetailView(AcademicAdminRequiredMixin, View):
    """Spec §7 'Student profiles', 'Guardian information', 'Academic
    history', 'Student status', 'Student documents'."""

    template_name = "dashboard/academic_admin/student_detail.html"
    active_nav = "students"

    def get(self, request, student_id):
        school = self.get_school(request)
        student = get_object_or_404(
            Student.objects.select_related("user", "current_class", "current_stream"),
            pk=student_id, school=school,
        )
        guardians = StudentGuardian.objects.filter(student=student).select_related("guardian")
        enrollments = Enrollment.objects.filter(student=student).select_related(
            "class_subject__subject", "academic_year"
        )
        return render(request, self.template_name, {
            "student": student, "active": self.active_nav,
            "guardians": guardians, "enrollments": enrollments,
            "status_choices": Student.Status.choices,
        })

    def post(self, request, student_id):
        school = self.get_school(request)
        student = get_object_or_404(Student, pk=student_id, school=school)
        try:
            change_student_status(
                student=student, new_status=request.POST.get("status"),
                changed_by=request.user, reason=request.POST.get("reason", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:academic_admin_student_detail", student_id=student.pk)


class AcademicAdminResultsApprovalView(AcademicAdminRequiredMixin, TemplateView):
    """Spec §7/§14: the Academic Admin review/verify/approve/publish
    steps in the result-processing workflow. Reuses
    services.transition_assessment_workflow() (Phase 8, already tested —
    including the 'a teacher can't approve their own results' rule)."""

    template_name = "dashboard/academic_admin/results_approval.html"
    active_nav = "results"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        pending_statuses = [
            Assessment.WorkflowStatus.SUBMITTED, Assessment.WorkflowStatus.REVIEWED,
            Assessment.WorkflowStatus.VERIFIED,
        ]
        assessments = Assessment.objects.filter(
            class_subject__class_group__school=school, workflow_status__in=pending_statuses
        ).select_related(
            "class_subject__class_group", "class_subject__subject", "term"
        ).order_by("workflow_status") if school else []
        context.update({"school": school, "assessments": assessments})
        return context

    def post(self, request):
        school = self.get_school(request)
        assessment = get_object_or_404(
            Assessment, pk=request.POST.get("assessment_id"),
            class_subject__class_group__school=school,
        )
        action = request.POST.get("action")
        next_status_map = {
            "review": Assessment.WorkflowStatus.REVIEWED,
            "verify": Assessment.WorkflowStatus.VERIFIED,
            "approve": Assessment.WorkflowStatus.APPROVED,
            "publish": Assessment.WorkflowStatus.PUBLISHED,
            "reject": Assessment.WorkflowStatus.DRAFT,
        }
        to_status = next_status_map.get(action)
        if to_status is None:
            return HttpResponseForbidden("Unknown action.")
        try:
            transition_assessment_workflow(
                assessment=assessment, to_status=to_status, actor=request.user,
                request=request,
            )
        except (ValueError, PermissionError) as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:academic_admin_results_approval")


class AcademicAdminAttendanceCorrectionView(AcademicAdminRequiredMixin, TemplateView):
    """Spec §7/§11: 'Academic Admin can... correct attendance with
    appropriate permissions.' Reuses services.correct_attendance_record()
    (Phase 6, already tested — including the full audit-log trail)."""

    template_name = "dashboard/academic_admin/attendance_correction.html"
    active_nav = "attendance"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        target_date = self.request.GET.get("date") or datetime.date.today().isoformat()

        records = AttendanceRecord.objects.filter(
            session__class_subject__class_group__school=school, session__date=target_date,
        ).select_related(
            "student__user", "session__class_subject__subject", "session__class_subject__class_group",
        ) if school else AttendanceRecord.objects.none()

        context.update({
            "school": school, "records": records, "target_date": target_date,
            "status_choices": AttendanceRecord.Status.choices,
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        record = get_object_or_404(
            AttendanceRecord, pk=request.POST.get("record_id"),
            session__class_subject__class_group__school=school,
        )
        correct_attendance_record(
            record=record, new_status=request.POST.get("status"),
            corrected_by=request.user, request=request,
            new_notes=request.POST.get("notes", ""),
        )
        return redirect(
            f"{reverse('dashboard:academic_admin_attendance_correction')}"
            f"?date={record.session.date.isoformat()}"
        )