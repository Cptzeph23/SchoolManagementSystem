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
    path(
        "transcripts/<int:student_id>/download/",
        views.TranscriptGenerateAndDownloadView.as_view(), name="transcript_download",
    ),
    path(
        "transcripts/verify/<uuid:verification_code>/",
        views.TranscriptVerifyView.as_view(), name="transcript_verify",
    ),
    path("student/", views.StudentDashboardView.as_view(), name="student_dashboard"),
    path("student/academic/", views.StudentAcademicView.as_view(), name="student_academic"),
    path("student/lms/", views.StudentLMSView.as_view(), name="student_lms"),
    path(
        "student/lms/assignments/<int:assignment_id>/submit/",
        views.StudentSubmitAssignmentView.as_view(), name="student_submit_assignment",
    ),
    path("student/finance/", views.StudentFinanceView.as_view(), name="student_finance"),
    path(
        "student/communication/", views.StudentCommunicationView.as_view(),
        name="student_communication",
    ),
    path(
        "student/notifications/<int:notification_id>/read/",
        views.StudentMarkNotificationReadView.as_view(), name="student_mark_notification_read",
    ),
    path("parent/", views.ParentDashboardView.as_view(), name="parent_dashboard"),
    path(
        "parent/children/<int:student_id>/academic/",
        views.ParentChildAcademicView.as_view(), name="parent_child_academic",
    ),
    path(
        "parent/children/<int:student_id>/finance/",
        views.ParentChildFinanceView.as_view(), name="parent_child_finance",
    ),
    path(
        "parent/communication/", views.ParentCommunicationView.as_view(),
        name="parent_communication",
    ),
    path(
        "parent/notifications/<int:notification_id>/read/",
        views.ParentMarkNotificationReadView.as_view(), name="parent_mark_notification_read",
    ),
]