# Absolute path: SMS/smsApp/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    AcademicYear,
    Assessment,
    AssessmentComponent,
    AssessmentMark,
    AssessmentStructure,
    AssessmentType,
    AttendanceRecord,
    AttendanceSession,
    AuditLog,
    Campus,
    Class,
    ClassSubject,
    Department,
    Enrollment,
    GradeBand,
    GradingScheme,
    Guardian,
    LoginHistory,
    Program,
    School,
    Staff,
    StaffQualification,
    Stream,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
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


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only in admin — audit rows must never be edited/deleted from
    the UI (spec §37/§38 'never silently change'), only written via
    smsApp.services.log_audit()."""

    list_display = ("created_at", "actor", "action", "target_model", "target_object_id", "ip_address")
    list_filter = ("action", "target_model")
    search_fields = ("actor__username", "target_model", "target_object_id", "description")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "was_successful", "ip_address")
    list_filter = ("was_successful",)
    search_fields = ("user__username",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "department", "subject_type", "credit_hours", "is_active")
    list_filter = ("school", "department", "subject_type", "is_active")
    search_fields = ("name", "code")
    filter_horizontal = ("prerequisites",)


@admin.register(ClassSubject)
class ClassSubjectAdmin(admin.ModelAdmin):
    list_display = ("subject", "class_group", "is_active")
    list_filter = ("class_group__school", "is_active")
    search_fields = ("subject__name", "class_group__name")


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("class_subject", "teacher", "term", "is_active")
    list_filter = ("term", "is_active")
    search_fields = ("teacher__staff_id", "class_subject__subject__name")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "class_subject", "academic_year", "status", "enrolled_on")
    list_filter = ("academic_year", "status")
    search_fields = ("student__admission_number", "class_subject__subject__name")


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("class_subject", "date", "term", "taken_by", "is_locked")
    list_filter = ("term", "is_locked", "class_subject__class_group__school")
    search_fields = ("class_subject__subject__name",)
    inlines = [AttendanceRecordInline]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    """Direct editing here bypasses the audit trail in
    smsApp.services.correct_attendance_record() — kept available for Super
    Admin emergency fixes, but the in-app correction workflow (Phase 7+)
    should be the normal path so corrections are logged."""

    list_display = ("student", "session", "status", "recorded_by", "updated_at")
    list_filter = ("status", "session__term")
    search_fields = ("student__admission_number",)


@admin.register(AssessmentType)
class AssessmentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("name", "code")


class AssessmentComponentInline(admin.TabularInline):
    model = AssessmentComponent
    extra = 1


@admin.register(AssessmentStructure)
class AssessmentStructureAdmin(admin.ModelAdmin):
    list_display = ("name", "term", "subject", "school", "is_active")
    list_filter = ("school", "term", "is_active")
    search_fields = ("name",)
    inlines = [AssessmentComponentInline]

    def total_weight(self, obj):
        return sum(c.weight_percentage for c in obj.components.all())
    total_weight.short_description = "Total weight %"


class GradeBandInline(admin.TabularInline):
    model = GradeBand
    extra = 1


@admin.register(GradingScheme)
class GradingSchemeAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "is_default", "is_active")
    list_filter = ("school", "is_default", "is_active")
    search_fields = ("name",)
    inlines = [GradeBandInline]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ("title", "class_subject", "term", "component", "date", "is_published")
    list_filter = ("term", "is_published", "class_subject__class_group__school")
    search_fields = ("title",)


@admin.register(AssessmentMark)
class AssessmentMarkAdmin(admin.ModelAdmin):
    list_display = ("student", "assessment", "marks_obtained", "recorded_by", "updated_at")
    list_filter = ("assessment__term",)
    search_fields = ("student__admission_number",)