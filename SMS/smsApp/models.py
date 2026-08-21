
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