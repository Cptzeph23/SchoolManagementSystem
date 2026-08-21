# Absolute path: SMS/smsApp/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    AcademicYear,
    Campus,
    Class,
    Department,
    Guardian,
    Program,
    School,
    Staff,
    StaffQualification,
    Stream,
    Student,
    StudentGuardian,
    Term,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "is_active", "is_locked", "is_staff")
    list_filter = ("role", "is_active", "is_locked", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("SMS Profile", {"fields": ("role", "phone_number", "is_locked")}),
    )


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "created_at")
    search_fields = ("name", "code")


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "is_main", "is_active")
    list_filter = ("school", "is_main", "is_active")
    search_fields = ("name", "code")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "campus", "head", "is_active")
    list_filter = ("school", "campus", "is_active")
    search_fields = ("name", "code")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "program_type", "is_active")
    list_filter = ("school", "program_type", "is_active")
    search_fields = ("name", "code")


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "start_date", "end_date", "is_current")
    list_filter = ("school", "is_current")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "term_number", "start_date", "end_date", "is_current")
    list_filter = ("academic_year__school", "is_current")


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "school", "campus", "class_teacher", "is_active")
    list_filter = ("school", "program", "campus", "is_active")
    search_fields = ("name",)


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    list_display = ("name", "class_group", "capacity", "is_active")
    list_filter = ("class_group__school", "is_active")
    search_fields = ("name",)


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "relationship", "phone_number", "school", "is_active")
    list_filter = ("school", "relationship", "is_active")
    search_fields = ("first_name", "last_name", "phone_number", "email", "national_id")


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 1


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "admission_number", "user", "school", "current_class", "current_stream",
        "status", "is_active",
    )
    list_filter = ("school", "status", "current_class", "current_stream", "is_active")
    search_fields = ("admission_number", "user__first_name", "user__last_name", "national_id")
    inlines = [StudentGuardianInline]


class StaffQualificationInline(admin.TabularInline):
    model = StaffQualification
    extra = 1


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = (
        "staff_id", "user", "school", "department", "job_title",
        "employment_status", "employment_type", "is_active",
    )
    list_filter = ("school", "department", "employment_status", "employment_type", "is_active")
    search_fields = ("staff_id", "user__first_name", "user__last_name", "job_title")
    inlines = [StaffQualificationInline]