# Absolute path: SMS/smsApp/urls.py
from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="dashboard:login"), name="logout"),
    path("account-locked/", views.AccountLockedView.as_view(), name="account_locked"),
    path("", views.DashboardRouterView.as_view(), name="home"),
    path("coming-soon/", views.ComingSoonView.as_view(), name="coming_soon"),
    path("super-admin/", views.SuperAdminDashboardView.as_view(), name="super_admin"),
    path(
        "reports/<int:report_card_id>/html/",
        views.ReportCardHTMLView.as_view(), name="report_card_html",
    ),
    path(
        "reports/<int:report_card_id>/pdf/",
        views.ReportCardPDFView.as_view(), name="report_card_pdf",
    ),
    path("reports/batch-generate/", views.BatchReportGenerateView.as_view(), name="report_batch_generate"),
]