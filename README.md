# SMS + LMS Platform

Production-ready School Management System integrated with a Learning
Management System. Built on the existing `SMS` project / `smsApp` app
structure. Django backend (source of truth), Django-template web
frontend, REST API layer for a future Flutter app, PostgreSQL via
Supabase, Supabase Storage for files.


---


### Structure

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

