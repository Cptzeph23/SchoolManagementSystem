"""
SMS + LMS Platform — Root URL configuration
Path in repo: SMS/SMS/urls.py

Phase 1A only wires up:
  - Django admin (verifies DB connectivity + migrations work)
  - a health-check endpoint

Business-domain URLs (students, academics, LMS, finance, etc.) get
included here app-by-app as each is built, e.g.:
  path("students/", include("students.urls"))
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def health_check(request):
    return JsonResponse({"status": "ok", "service": "sms-lms-platform"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health-check"),
]
