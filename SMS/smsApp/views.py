# Absolute path: SMS/smsApp/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .models import (
    AcademicYear,
    Announcement,
    Assignment,
    AssignmentSubmission,
    Class,
    ClassSubject,
    CourseMaterial,
    Discussion,
    Guardian,
    Invoice,
    Notification,
    Payment,
    Quiz,
    QuizAttempt,
    ReportCard,
    ReportTemplate,
    Staff,
    Student,
    Term,
    Transcript,
    User,
)
from .permissions import RoleRequiredMixin
from .services import (
    compute_student_account_summary,
    compute_weighted_average,
    generate_batch_reports,
    generate_report_pdf,
    generate_transcript,
    get_children_for_guardian,
    get_dashboard_url_for_role,
    get_grade_for_mark,
    mark_notification_read,
    record_login,
    render_report_html,
    submit_assignment,
    verify_transcript,
)


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
            submit_assignment(
                assignment=assignment, student=student,
                submitted_file=request.FILES.get("submitted_file"),
                submitted_text=request.POST.get("submitted_text", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))

        return redirect("dashboard:student_lms")


class StudentFinanceView(StudentRequiredMixin, TemplateView):
    """Spec §17 Finance section: fees, balance, invoices, receipts,
    payment history. This is the student's own account — showing full
    detail here is showing someone their own data, not a policy
    violation; §19/§23's 'do not expose financial info to unrelated
    staff' is a different boundary from 'a student sees their own bill'."""

    template_name = "dashboard/student/finance.html"

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