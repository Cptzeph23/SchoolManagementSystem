# Path in repo: SMS/smsApp/models.py
from django.db import models  # noqa: F401

# Phase 1A intentionally ships no models yet. Phase 1B adds:
#   - TimeStampedModel / SoftDeleteModel (shared abstract base models)
#   - the custom User model (wired up as AUTH_USER_MODEL before the
#     first migration touches auth tables)
