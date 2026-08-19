# SMS + LMS Platform

Production-ready School Management System integrated with a Learning
Management System. Built on the existing `SMS` project / `smsApp` app
structure. Django backend (source of truth), Django-template web
frontend, REST API layer for a future Flutter app, PostgreSQL via
Supabase, Supabase Storage for files.

Built **incrementally, in phases**. Each phase is implemented, verified,
and confirmed before the next begins.

---

## Phase 1A — Project Inception (this delivery)

**Goal:** make the existing `SMS` / `smsApp` skeleton boot cleanly
against a PostgreSQL/Supabase database, with environment-aware settings,
security defaults, and the supporting files a real repo needs
(`.gitignore`, `.env.example`, `requirements.txt`). No business models,
no auth customization yet — that's Phase 1B.

### Structure (unchanged from what you already had, extended)

```
SMS/                        ← repo root
├── manage.py
├── requirements.txt         [NEW]
├── .env.example              [NEW]
├── .gitignore                 [NEW]
├── README.md                   [NEW]
├── SMS/
│   ├── __init__.py
│   ├── settings.py            [FILLED IN — was empty/default]
│   ├── urls.py                [FILLED IN]
│   ├── wsgi.py                [FILLED IN]
│   └── asgi.py                [FILLED IN]
├── smsApp/
│   ├── __init__.py
│   ├── admin.py                [placeholder, ready for Phase 1B]
│   ├── apps.py                 [SmsappConfig]
│   ├── models.py                [placeholder, ready for Phase 1B]
│   ├── views.py                 [placeholder]
│   ├── tests.py                  [placeholder]
│   └── migrations/
│       └── __init__.py
├── templates/
│   └── base.html                 [NEW — Bootstrap 5 shell]
├── static/
│   └── css/base.css               [NEW]
├── media/                          [NEW, empty, for future uploads]
└── logs/                            [NEW, empty, for django.log]
```

Nothing was deleted or restructured. `smsApp` stays as your first app —
it becomes the home for shared/core concerns (base abstract models, the
custom `User` model, RBAC) starting Phase 1B, rather than spinning up a
separate `core` app right away.

### Why `settings.py` is one file, not a settings/ package

Your project already has a single `SMS/settings.py`. Rather than
restructuring that into `settings/base.py` + `development.py` +
`production.py`, this settings.py reads one `DJANGO_ENV` variable and
branches internally:

- `DJANGO_ENV=development` (default) → `DEBUG` readable from `.env`, console email backend
- `DJANGO_ENV=production` → `DEBUG` locked `False`, HTTPS/HSTS/secure-cookie block turns on, DB connection forces `sslmode=require`, SMTP email backend
- `DJANGO_ENV=testing` → in-memory SQLite, fast password hasher, `locmem` email backend

If you'd rather move to a split `settings/` package later (useful once
the file gets long), that's a clean, low-risk refactor we can do in a
dedicated phase — not necessary right now.

### Setup instructions

```bash
cd SMS

# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# edit .env: set DJANGO_SECRET_KEY and DATABASE_URL at minimum

# 4. Verify the project boots and can reach the database
python manage.py check
python manage.py migrate        # Django's built-in auth/admin/session tables only

# 5. Create a superuser
python manage.py createsuperuser

# 6. Run the dev server
python manage.py runserver
```

Then visit:
- `http://127.0.0.1:8000/health/` → `{"status": "ok", "service": "sms-lms-platform"}`
- `http://127.0.0.1:8000/admin/` → Django admin login

Both loading confirms: settings load correctly, the Supabase Postgres
connection works, static files serve, and `smsApp` is registered
without errors.

---

## Next phase — Phase 1B (proposed)

Once you confirm Phase 1A runs cleanly on your machine:

1. Add `smsApp/models.py`: `TimeStampedModel` / `SoftDeleteModel`
   abstract base classes, and the custom `User` model (email-based
   login, ready for role/permission assignment).
2. Set `AUTH_USER_MODEL = "smsApp.User"` in `settings.py` **before**
   running any migrations against it.
3. Run `makemigrations` / `migrate` against the custom user model.
4. Register `User` in `smsApp/admin.py`.
5. Add the first tests in `smsApp/tests.py` (model creation + admin
   access), per the master spec's testing requirements.

We won't start Phase 1B until you confirm `python manage.py check`,
`migrate`, `/health/`, and `/admin/` all work for you first.

---

## Full phase roadmap (high level)

1. **Phase 1** — Project inception, settings, custom User, RBAC foundation
2. **Phase 2** — Django REST Framework, JWT auth, API versioning skeleton
3. **Phase 3** — School/Academic structure (School, AcademicYear, Term, Department, Program, Class, Stream)
4. **Phase 4** — Students & Guardians
5. **Phase 5** — Staff & HR
6. **Phase 6** — Curriculum (Subjects/Courses, Teaching Assignments, Timetable)
7. **Phase 7** — Attendance
8. **Phase 8** — LMS (materials, assignments, quizzes, submissions, discussions)
9. **Phase 9** — Assessment & Grading engine
10. **Phase 10** — Result workflow (entry → review → approval → publication → amendments)
11. **Phase 11** — Report books & transcripts (PDF generation)
12. **Phase 12** — Finance (fee structures, invoicing, payments, receipts, refunds)
13. **Phase 13** — Library
14. **Phase 14** — Communication (announcements, notifications, email/SMS-ready)
15. **Phase 15** — Audit logging (system-wide)
16. **Phase 16** — Dashboards & analytics (Chart.js) per role
17. **Phase 17** — Supabase Storage integration (files/documents/media)
18. **Phase 18** — Background processing (Celery + Redis)
19. **Phase 19** — Hardening, testing pass, deployment prep (Render)
20. **Phase 20** — Flutter mobile app (starts only once the API is stable)

Each phase ships as downloadable files with explicit repo paths — never
as one large undifferentiated code dump.
