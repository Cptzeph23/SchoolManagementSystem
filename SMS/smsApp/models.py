"""
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
#Academic structure
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