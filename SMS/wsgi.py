"""
SMS + LMS Platform — WSGI entry point
Path in repo: SMS/SMS/wsgi.py
Used by production WSGI servers (e.g. gunicorn) via `SMS.wsgi:application`.
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "SMS.settings")

application = get_wsgi_application()
