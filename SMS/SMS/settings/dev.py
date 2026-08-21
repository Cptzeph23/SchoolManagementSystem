"""
Development settings.
"""
from .base import *  
from .base import env, BASE_DIR

DEBUG = True

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# ---------------------------------------------------------------------------
# Database — Supabase PostgreSQL (falls back to local sqlite if DATABASE_URL
# is not set, so a fresh clone can still run `migrate` without Supabase creds)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

