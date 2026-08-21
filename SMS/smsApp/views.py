# Absolute path: SMS/smsApp/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import RedirectView, TemplateView

from .models import AcademicYear, Class, Staff, Student, Term, User
from .permissions import RoleRequiredMixin
from .services import get_dashboard_url_for_role, record_login


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
        return get_dashboard_url_for_role(self.request.user.role)


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