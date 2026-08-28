"""Absolute path: SMS/SMS/urls.py"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("smsApp.urls")),
    # Spec §25: versioned API + OpenAPI/Swagger-compatible documentation.
    path("api/v1/", include("smsApp.api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api_docs"),
]