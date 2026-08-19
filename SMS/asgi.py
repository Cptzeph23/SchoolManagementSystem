"""
SMS + LMS Platform — ASGI entry point
Path in repo: SMS/SMS/asgi.py
Reserved for future async use (e.g. websockets for live notifications).
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "SMS.settings")

application = get_asgi_application()
