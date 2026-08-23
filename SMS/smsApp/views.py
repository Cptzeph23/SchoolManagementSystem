# Absolute path: SMS/smsApp/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .models import AcademicYear, Class, ReportCard, ReportTemplate, Staff, Student, Term, User
from .permissions import RoleRequiredMixin
from .services import (
    generate_batch_reports,
    generate_report_pdf,
    get_dashboard_url_for_role,
    record_login,
    render_report_html,
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
    (Ctrl/Cmd+P), so no separate 'printable' code path is needed."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER, User.Role.STAFF_ADMIN,
    ]

    def get(self, request, report_card_id):
        report_card = get_object_or_404(ReportCard, pk=report_card_id)
        html = render_report_html(report_card=report_card)
        return HttpResponse(html)


class ReportCardPDFView(RoleRequiredMixin, View):
    """Spec §15 'PDF report' / 'Downloadable report'. Generates on first
    request if no PDF exists yet, then serves the stored file — repeat
    downloads don't re-render unless explicitly regenerated."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER, User.Role.STAFF_ADMIN,
    ]

    def get(self, request, report_card_id):
        report_card = get_object_or_404(ReportCard, pk=report_card_id)
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