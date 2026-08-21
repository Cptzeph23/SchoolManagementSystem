"""
Absolute path: SMS/smsApp/models.py

Phase 1B — Custom User model + RBAC foundation.

Design notes:
- `role` is a coarse label used for dashboard routing / UI branching
  (e.g. "show Finance Admin sidebar").
- Actual authorization decisions (§4 of spec) must use Django's
  built-in Group + Permission framework, NOT `role` alone — this
  satisfies "use permissions in addition to roles, do not rely
  solely on hard-coded role checks."
- Fine-grained permissions (students.view, results.approve, etc.)
  are declared via `Meta.permissions` on the relevant domain models
  as those apps are built (Phase 3+), then attached to Groups
  matching each Role via the admin or a data migration.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
        STAFF_ADMIN = "STAFF_ADMIN", "Staff Admin"
        ACADEMIC_ADMIN = "ACADEMIC_ADMIN", "Academic Admin"
        FINANCE_ADMIN = "FINANCE_ADMIN", "Finance Admin"
        TEACHER = "TEACHER", "Teacher/Lecturer"
        EXAM_OFFICER = "EXAM_OFFICER", "Examination Officer"
        CLASS_TEACHER = "CLASS_TEACHER", "Class Teacher"
        DEPARTMENT_HEAD = "DEPARTMENT_HEAD", "Department Head"
        ACCOUNTANT = "ACCOUNTANT", "Accountant/Finance Officer"
        LIBRARIAN = "LIBRARIAN", "Librarian"
        STUDENT = "STUDENT", "Student"
        PARENT = "PARENT", "Parent/Guardian"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
        db_index=True,
        help_text="Coarse role for UI/dashboard routing. Authorization "
                   "decisions must still check Django permissions.",
    )
    phone_number = models.CharField(max_length=20, blank=True)
    is_locked = models.BooleanField(
        default=False,
        help_text="Account lock distinct from is_active — used for "
                   "Super Admin 'lock/unlock account' action (§5).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


# =============================================================================
# Phase 2 — Academic structure
# Hierarchy (spec §7): AcademicYear → Term → Department → Program → Class
#                      → Subject/Course → Teacher
# School/Campus/Stream are siblings supporting that hierarchy (spec §24).
# Multi-school scalability (spec §3 "Scalability") is built in now via the
# School FK on every node, even though the first deployment serves one school.
# =============================================================================

class School(models.Model):
    """Root tenant. Every other academic-structure model traces back here
    so the schema is multi-school-ready without a later migration."""

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20, unique=True)
    motto = models.CharField(max_length=255, blank=True)
    logo = models.ImageField(upload_to="school/logos/", blank=True, null=True)
    address = models.TextField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    established_date = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "schools"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Campus(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="campuses")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    is_main = models.BooleanField(
        default=False, help_text="Marks the school's primary/head campus."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "campuses"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_campus_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Department(models.Model):
    """Academic or administrative department (spec §5, §7).
    `head` intentionally points at User, not a Staff model — Staff profiles
    land in Phase 3; role is filtered so only staff-capable users qualify."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="departments")
    campus = models.ForeignKey(
        Campus, on_delete=models.SET_NULL, related_name="departments",
        blank=True, null=True,
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    head = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="departments_headed",
        blank=True, null=True,
        limit_choices_to={"role__in": ["DEPARTMENT_HEAD", "TEACHER", "STAFF_ADMIN"]},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "departments"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_department_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Program(models.Model):
    """e.g. '8-4-4', 'CBC', 'Cambridge IGCSE', 'Bachelor of Science' —
    supports both school-style and university-style structures (spec §7)."""

    class ProgramType(models.TextChoices):
        SCHOOL = "SCHOOL", "School-style (Grade/Class based)"
        UNIVERSITY = "UNIVERSITY", "University-style (Credit/Course based)"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="programs")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="programs",
        blank=True, null=True,
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    program_type = models.CharField(
        max_length=15, choices=ProgramType.choices, default=ProgramType.SCHOOL
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "programs"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_program_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class AcademicYear(models.Model):
    """e.g. '2026/2027'. Top of the academic hierarchy (spec §7)."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="academic_years")
    name = models.CharField(max_length=20, help_text="e.g. '2026/2027'")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academic_years"
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"], name="uniq_academic_year_per_school"
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="academic_year_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"

    def save(self, *args, **kwargs):
        # Only one current academic year per school (dashboard §5 relies on this).
        if self.is_current:
            AcademicYear.objects.filter(
                school=self.school, is_current=True
            ).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class Term(models.Model):
    """Term or Semester within an AcademicYear (spec §7 'Term/Semester')."""

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="terms"
    )
    name = models.CharField(max_length=50, help_text="e.g. 'Term 1', 'Semester 1'")
    term_number = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "terms"
        ordering = ["academic_year", "term_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "term_number"], name="uniq_term_number_per_year"
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="term_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} - {self.academic_year.name}"

    def save(self, *args, **kwargs):
        if self.is_current:
            Term.objects.filter(
                academic_year__school=self.academic_year.school, is_current=True
            ).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class Class(models.Model):
    """e.g. 'Grade 10', 'Form 2', 'Year 1 Computer Science'."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="classes")
    campus = models.ForeignKey(
        Campus, on_delete=models.SET_NULL, related_name="classes", blank=True, null=True
    )
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="classes")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="classes", blank=True, null=True
    )
    name = models.CharField(max_length=100, help_text="e.g. 'Grade 10', 'Form 2'")
    level_order = models.PositiveSmallIntegerField(
        default=0, help_text="Sort order for progression, e.g. Grade 1=1, Grade 2=2."
    )
    class_teacher = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="classes_led",
        blank=True, null=True,
        limit_choices_to={"role__in": ["CLASS_TEACHER", "TEACHER"]},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "classes"
        ordering = ["school", "program", "level_order", "name"]
        verbose_name_plural = "Classes"
        constraints = [
            models.UniqueConstraint(
                fields=["program", "name"], name="uniq_class_name_per_program"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.program.name})"


class Stream(models.Model):
    """e.g. 'Stream A', 'Blue Stream' — subdivision of a Class."""

    class_group = models.ForeignKey(Class, on_delete=models.CASCADE, related_name="streams")
    name = models.CharField(max_length=50)
    capacity = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "streams"
        ordering = ["class_group", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "name"], name="uniq_stream_name_per_class"
            )
        ]

    def __str__(self) -> str:
        return f"{self.class_group.name} - {self.name}"


# =============================================================================
# Phase 3 — People: Guardian, Student, Staff
# Spec refs: §6 (Staff management), §7 (Student management), §24 (DB design),
#            §36 (unique admission number), §39 (multi-school readiness)
# =============================================================================

class Guardian(models.Model):
    """Parent/Guardian contact record. Deliberately NOT a `User` by default —
    spec §18 implies a Parent Portal, but a guardian record must be able to
    exist before any portal login is issued (e.g. entered at admission time).
    `user` is optional and links back once a portal account is created."""

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.SET_NULL, related_name="guardian_profile",
        blank=True, null=True,
        limit_choices_to={"role": "PARENT"},
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="guardians")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    relationship = models.CharField(
        max_length=50, help_text="e.g. 'Mother', 'Father', 'Uncle', 'Legal Guardian'"
    )
    phone_number = models.CharField(max_length=20)
    alternate_phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    national_id = models.CharField(max_length=50, blank=True)
    occupation = models.CharField(max_length=150, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "guardians"
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} ({self.relationship})"


class Student(models.Model):
    """Spec §7 statuses reproduced verbatim; §36 requires a unique admission
    number (unique per school, not globally, per §39 multi-school readiness).
    `photo`/`documents` use local storage now — swapped to Supabase Storage
    in Phase 17 (spec §6/§29) without changing this model's public API."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        GRADUATED = "GRADUATED", "Graduated"
        SUSPENDED = "SUSPENDED", "Suspended"
        TRANSFERRED = "TRANSFERRED", "Transferred"
        DEFERRED = "DEFERRED", "Deferred"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        EXPELLED = "EXPELLED", "Expelled"
        ALUMNI = "ALUMNI", "Alumni"

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.CASCADE, related_name="student_profile",
        limit_choices_to={"role": "STUDENT"},
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="students")
    admission_number = models.CharField(max_length=30)
    admission_date = models.DateField()
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    photo = models.ImageField(upload_to="students/photos/", blank=True, null=True)
    national_id = models.CharField(
        max_length=50, blank=True, help_text="Birth certificate no. or national ID."
    )
    current_class = models.ForeignKey(
        Class, on_delete=models.SET_NULL, related_name="students", blank=True, null=True
    )
    current_stream = models.ForeignKey(
        Stream, on_delete=models.SET_NULL, related_name="students", blank=True, null=True
    )
    program = models.ForeignKey(
        Program, on_delete=models.SET_NULL, related_name="students", blank=True, null=True
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    guardians = models.ManyToManyField(
        Guardian, through="StudentGuardian", related_name="students"
    )
    address = models.TextField(blank=True)
    blood_group = models.CharField(max_length=5, blank=True)
    medical_notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "students"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "admission_number"],
                name="uniq_admission_number_per_school",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name() or self.user.username} ({self.admission_number})"


class StudentGuardian(models.Model):
    """Through-table for Student<->Guardian (§7 'Guardian information').
    A student can have multiple guardians; one is flagged primary contact."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE)
    is_primary_contact = models.BooleanField(default=False)
    is_emergency_contact = models.BooleanField(default=True)
    is_billing_contact = models.BooleanField(
        default=False,
        help_text="Who fee invoices/statements are addressed to (Finance module, Phase 19).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "student_guardians"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "guardian"], name="uniq_student_guardian_pair"
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.guardian}"


class Staff(models.Model):
    """Spec §6 Staff management fields. `employment_status` distinct from
    `User.is_active`/`is_locked` — a staff member can be ON_LEAVE while their
    login stays active. Qualifications/certifications/documents modeled as
    separate tables (below) rather than JSON/text blobs, per DRY (§3) and so
    Training/Performance (§6 'Staff performance') can reference them later."""

    class EmploymentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_LEAVE = "ON_LEAVE", "On Leave"
        SUSPENDED = "SUSPENDED", "Suspended"
        TERMINATED = "TERMINATED", "Terminated"
        RETIRED = "RETIRED", "Retired"
        RESIGNED = "RESIGNED", "Resigned"

    class EmploymentType(models.TextChoices):
        FULL_TIME = "FULL_TIME", "Full-Time"
        PART_TIME = "PART_TIME", "Part-Time"
        CONTRACT = "CONTRACT", "Contract"
        INTERN = "INTERN", "Intern"

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.CASCADE, related_name="staff_profile",
        limit_choices_to={
            "role__in": [
                "STAFF_ADMIN", "ACADEMIC_ADMIN", "FINANCE_ADMIN", "TEACHER",
                "EXAM_OFFICER", "CLASS_TEACHER", "DEPARTMENT_HEAD",
                "ACCOUNTANT", "LIBRARIAN",
            ]
        },
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="staff_members")
    staff_id = models.CharField(max_length=30)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="staff_members",
        blank=True, null=True,
    )
    job_title = models.CharField(max_length=150)
    employment_type = models.CharField(
        max_length=15, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME
    )
    employment_status = models.CharField(
        max_length=15, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE,
        db_index=True,
    )
    date_hired = models.DateField()
    date_left = models.DateField(blank=True, null=True)
    photo = models.ImageField(upload_to="staff/photos/", blank=True, null=True)
    national_id = models.CharField(max_length=50, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staff"
        verbose_name_plural = "Staff"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "staff_id"], name="uniq_staff_id_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name() or self.user.username} ({self.staff_id})"


class StaffQualification(models.Model):
    """Spec §6 'Qualifications' / 'Certifications' as first-class rows,
    not a free-text field — supports future filtering/reporting (§32)."""

    class QualificationType(models.TextChoices):
        DEGREE = "DEGREE", "Degree"
        DIPLOMA = "DIPLOMA", "Diploma"
        CERTIFICATE = "CERTIFICATE", "Certificate"
        LICENSE = "LICENSE", "Professional License"
        OTHER = "OTHER", "Other"

    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="qualifications"
    )
    qualification_type = models.CharField(
        max_length=15, choices=QualificationType.choices
    )
    title = models.CharField(max_length=255, help_text="e.g. 'B.Ed Mathematics'")
    institution = models.CharField(max_length=255, blank=True)
    year_obtained = models.PositiveSmallIntegerField(blank=True, null=True)
    document = models.FileField(
        upload_to="staff/qualifications/", blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_qualifications"
        ordering = ["-year_obtained"]

    def __str__(self) -> str:
        return f"{self.title} - {self.staff}"


# =============================================================================
# Phase 4 — Audit logging + login history
# Spec refs: §5 (audit logs), §27 (audit logging as a security requirement),
#            §33 (audit logging), §37/§38 (never silently change data)
# =============================================================================

class AuditLog(models.Model):
    """Generic audit trail. Rows are written via `smsApp.services.log_audit()`
    (Phase 4 service layer), never edited or deleted from application code —
    enforced by omitting update/delete from ModelAdmin (see admin.py)."""

    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed Login"
        APPROVE = "APPROVE", "Approve"
        PUBLISH = "PUBLISH", "Publish"
        LOCK = "LOCK", "Lock Account"
        UNLOCK = "UNLOCK", "Unlock Account"
        ROLE_CHANGE = "ROLE_CHANGE", "Role Change"
        PERMISSION_CHANGE = "PERMISSION_CHANGE", "Permission Change"
        PASSWORD_RESET = "PASSWORD_RESET", "Password Reset"
        OTHER = "OTHER", "Other"

    actor = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="audit_logs",
        blank=True, null=True, help_text="Who performed the action.",
    )
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    target_model = models.CharField(
        max_length=100, blank=True, help_text="e.g. 'Student', 'User'."
    )
    target_object_id = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=255, blank=True)
    previous_value = models.JSONField(blank=True, null=True)
    new_value = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.actor} - {self.action} - {self.target_model} @ {self.created_at:%Y-%m-%d %H:%M}"


class LoginHistory(models.Model):
    """Spec §5 'View login history'. Separate from AuditLog's LOGIN entries
    so login-history queries (frequent, per-user) don't scan the whole
    audit trail table."""

    user = models.ForeignKey(
        "smsApp.User", on_delete=models.CASCADE, related_name="login_history"
    )
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True)
    was_successful = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "login_history"
        ordering = ["-created_at"]
        verbose_name_plural = "Login history"

    def __str__(self) -> str:
        status = "success" if self.was_successful else "failed"
        return f"{self.user} - {status} @ {self.created_at:%Y-%m-%d %H:%M}"