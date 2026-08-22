# Absolute path: SMS/smsApp/tests.py
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

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
    Class,
    ClassSubject,
    Enrollment,
    GradeBand,
    GradingScheme,
    Guardian,
    LoginHistory,
    Program,
    ResultAmendmentRequest,
    School,
    Staff,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
)
from .services import (
    correct_attendance_record,
    compute_weighted_average,
    decide_result_amendment,
    get_grade_for_mark,
    reject_assessment,
    request_result_amendment,
    transition_assessment_workflow,
    validate_grade_bands_no_overlap,
)

User = get_user_model()


class UserModelTests(TestCase):
    def test_user_created_with_default_student_role(self):
        user = User.objects.create_user(username="jdoe", password="pass12345")
        self.assertEqual(user.role, User.Role.STUDENT)

    def test_user_str_includes_role_display(self):
        user = User.objects.create_user(
            username="ateacher", password="pass12345", role=User.Role.TEACHER
        )
        self.assertIn("Teacher", str(user))


class AcademicStructureTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(
            school=self.school, name="8-4-4", code="844"
        )

    def test_school_code_must_be_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                School.objects.create(name="Other School", code="RVH")

    def test_only_one_current_academic_year_per_school(self):
        ay1 = AcademicYear.objects.create(
            school=self.school, name="2025/2026",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 12, 31),
            is_current=True,
        )
        ay2 = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            is_current=True,
        )
        ay1.refresh_from_db()
        self.assertFalse(ay1.is_current)
        self.assertTrue(ay2.is_current)

    def test_academic_year_end_before_start_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicYear.objects.create(
                    school=self.school, name="Bad Year",
                    start_date=datetime.date(2026, 12, 31),
                    end_date=datetime.date(2026, 1, 1),
                )

    def test_class_name_unique_per_program_not_globally(self):
        program2 = Program.objects.create(school=self.school, name="CBC", code="CBC")
        Class.objects.create(school=self.school, program=self.program, name="Grade 10")
        # Same class name allowed under a different program — should not raise.
        Class.objects.create(school=self.school, program=program2, name="Grade 10")

    def test_term_requires_unique_term_number_per_year(self):
        ay = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        Term.objects.create(
            academic_year=ay, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Term.objects.create(
                    academic_year=ay, name="Term 1 Duplicate", term_number=1,
                    start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 8, 1),
                )


class PeopleModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )

    def _make_student_user(self, username="student1"):
        return User.objects.create_user(
            username=username, password="pass12345", role=User.Role.STUDENT
        )

    def test_admission_number_unique_per_school_not_globally(self):
        school2 = School.objects.create(name="Lakeside Academy", code="LKA")
        Student.objects.create(
            user=self._make_student_user("s1"),
            school=self.school, admission_number="ADM001",
            admission_date=datetime.date(2026, 1, 10),
        )
        # Same admission number at a different school must be allowed.
        Student.objects.create(
            user=self._make_student_user("s2"),
            school=school2, admission_number="ADM001",
            admission_date=datetime.date(2026, 1, 10),
        )

    def test_duplicate_admission_number_same_school_rejected(self):
        Student.objects.create(
            user=self._make_student_user("s3"),
            school=self.school, admission_number="ADM002",
            admission_date=datetime.date(2026, 1, 10),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Student.objects.create(
                    user=self._make_student_user("s4"),
                    school=self.school, admission_number="ADM002",
                    admission_date=datetime.date(2026, 1, 10),
                )

    def test_student_default_status_is_active(self):
        student = Student.objects.create(
            user=self._make_student_user("s5"),
            school=self.school, admission_number="ADM003",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.assertEqual(student.status, Student.Status.ACTIVE)

    def test_guardian_linked_via_through_model_with_primary_contact_flag(self):
        student = Student.objects.create(
            user=self._make_student_user("s6"),
            school=self.school, admission_number="ADM004",
            admission_date=datetime.date(2026, 1, 10),
        )
        guardian = Guardian.objects.create(
            school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )
        link = StudentGuardian.objects.create(
            student=student, guardian=guardian, is_primary_contact=True
        )
        self.assertIn(guardian, student.guardians.all())
        self.assertTrue(link.is_primary_contact)

    def test_staff_id_unique_per_school(self):
        staff_user = User.objects.create_user(
            username="teacher1", password="pass12345", role=User.Role.TEACHER
        )
        Staff.objects.create(
            user=staff_user, school=self.school, staff_id="STF001",
            job_title="Mathematics Teacher", date_hired=datetime.date(2024, 1, 5),
        )
        staff_user2 = User.objects.create_user(
            username="teacher2", password="pass12345", role=User.Role.TEACHER
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Staff.objects.create(
                    user=staff_user2, school=self.school, staff_id="STF001",
                    job_title="Science Teacher", date_hired=datetime.date(2024, 2, 1),
                )


class AuthAndDashboardTests(TestCase):
    """Phase 4: login flow, role-based routing, access control."""

    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="admin1", password="pass12345", role=User.Role.SUPER_ADMIN
        )
        self.teacher = User.objects.create_user(
            username="teach1", password="pass12345", role=User.Role.TEACHER
        )

    def test_login_creates_login_history_and_audit_log(self):
        self.client.post(
            reverse("dashboard:login"),
            {"username": "admin1", "password": "pass12345"},
        )
        self.assertEqual(LoginHistory.objects.filter(user=self.super_admin).count(), 1)
        self.assertTrue(
            LoginHistory.objects.get(user=self.super_admin).was_successful
        )
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.super_admin, action=AuditLog.Action.LOGIN
            ).exists()
        )

    def test_failed_login_recorded(self):
        self.client.post(
            reverse("dashboard:login"),
            {"username": "admin1", "password": "wrong-password"},
        )
        self.assertTrue(
            LoginHistory.objects.filter(
                user=self.super_admin, was_successful=False
            ).exists()
        )

    def test_super_admin_router_redirects_to_super_admin_dashboard(self):
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:super_admin"))

    def test_non_super_admin_router_redirects_to_coming_soon(self):
        self.client.login(username="teach1", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:coming_soon"))

    def test_teacher_cannot_access_super_admin_dashboard(self):
        self.client.login(username="teach1", password="pass12345")
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertEqual(response.status_code, 403)

    def test_super_admin_dashboard_shows_live_student_count(self):
        school = School.objects.create(name="Riverside High", code="RVH2")
        student_user = User.objects.create_user(
            username="stud1", password="pass12345", role=User.Role.STUDENT
        )
        Student.objects.create(
            user=student_user, school=school, admission_number="ADM100",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertEqual(response.context["total_students"], 1)

    def test_locked_account_cannot_reach_dashboard(self):
        self.super_admin.is_locked = True
        self.super_admin.save()
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertRedirects(response, reverse("dashboard:account_locked"))

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:login"), response.url)

    def test_anonymous_user_hitting_root_redirected_not_500(self):
        """Regression test: DashboardRouterView must reject anonymous users
        before touching request.user.role, or it 500s with AttributeError
        on AnonymousUser."""
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:login"), response.url)

    def test_createsuperuser_account_gets_super_admin_role_and_dashboard(self):
        """Regression test: `createsuperuser` sets is_superuser=True but has
        no concept of our custom `role` field, so it would otherwise stay at
        the STUDENT default and misroute to the coming-soon page."""
        superuser = User.objects.create_superuser(
            username="rootadmin", email="root@example.com", password="pass12345"
        )
        self.assertEqual(superuser.role, User.Role.SUPER_ADMIN)

        self.client.login(username="rootadmin", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:super_admin"))


class CurriculumModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )

    def test_subject_code_unique_per_school_not_globally(self):
        school2 = School.objects.create(name="Lakeside Academy", code="LKA")
        Subject.objects.create(school=school2, code="MATH", name="Mathematics")  # allowed
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subject.objects.create(school=self.school, code="MATH", name="Maths Duplicate")

    def test_class_subject_pair_unique(self):
        ClassSubject.objects.create(class_group=self.class_group, subject=self.subject)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClassSubject.objects.create(class_group=self.class_group, subject=self.subject)

    def test_only_one_teacher_per_class_subject_per_term(self):
        class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        teacher_user1 = User.objects.create_user(
            username="t1", password="pass12345", role=User.Role.TEACHER
        )
        staff1 = Staff.objects.create(
            user=teacher_user1, school=self.school, staff_id="STF010",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        TeachingAssignment.objects.create(
            class_subject=class_subject, teacher=staff1, term=self.term
        )
        teacher_user2 = User.objects.create_user(
            username="t2", password="pass12345", role=User.Role.TEACHER
        )
        staff2 = Staff.objects.create(
            user=teacher_user2, school=self.school, staff_id="STF011",
            job_title="Maths Teacher 2", date_hired=datetime.date(2024, 1, 1),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeachingAssignment.objects.create(
                    class_subject=class_subject, teacher=staff2, term=self.term
                )

    def test_student_cannot_double_enroll_same_subject_same_year(self):
        class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        student_user = User.objects.create_user(
            username="s10", password="pass12345", role=User.Role.STUDENT
        )
        student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM200",
            admission_date=datetime.date(2026, 1, 10),
        )
        Enrollment.objects.create(
            student=student, class_subject=class_subject, academic_year=self.academic_year
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Enrollment.objects.create(
                    student=student, class_subject=class_subject,
                    academic_year=self.academic_year,
                )

    def test_enrollment_default_status_is_enrolled(self):
        class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        student_user = User.objects.create_user(
            username="s11", password="pass12345", role=User.Role.STUDENT
        )
        student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM201",
            admission_date=datetime.date(2026, 1, 10),
        )
        enrollment = Enrollment.objects.create(
            student=student, class_subject=class_subject, academic_year=self.academic_year
        )
        self.assertEqual(enrollment.status, Enrollment.Status.ENROLLED)

    def test_subject_prerequisites_are_not_symmetrical(self):
        advanced = Subject.objects.create(
            school=self.school, code="MATH2", name="Advanced Mathematics"
        )
        advanced.prerequisites.add(self.subject)
        self.assertIn(self.subject, advanced.prerequisites.all())
        self.assertNotIn(advanced, self.subject.prerequisites.all())


class AttendanceModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
        self.class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.admin_user = User.objects.create_user(
            username="acadadmin", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )
        student_user = User.objects.create_user(
            username="s20", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM300",
            admission_date=datetime.date(2026, 1, 10),
        )

    def test_only_one_session_per_class_subject_per_date(self):
        AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 1)
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AttendanceSession.objects.create(
                    class_subject=self.class_subject, term=self.term,
                    date=datetime.date(2026, 2, 1),
                )

    def test_only_one_record_per_student_per_session(self):
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 2)
        )
        AttendanceRecord.objects.create(
            session=session, student=self.student, status=AttendanceRecord.Status.PRESENT
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AttendanceRecord.objects.create(
                    session=session, student=self.student,
                    status=AttendanceRecord.Status.ABSENT,
                )

    def test_correct_attendance_record_updates_status_and_writes_audit_log(self):
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 3)
        )
        record = AttendanceRecord.objects.create(
            session=session, student=self.student, status=AttendanceRecord.Status.ABSENT
        )
        correct_attendance_record(
            record=record, new_status=AttendanceRecord.Status.PRESENT,
            corrected_by=self.admin_user, new_notes="Was marked absent by mistake",
        )
        record.refresh_from_db()
        self.assertEqual(record.status, AttendanceRecord.Status.PRESENT)
        self.assertEqual(record.recorded_by, self.admin_user)

        audit_entry = AuditLog.objects.filter(
            target_model="AttendanceRecord", target_object_id=str(record.pk)
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.previous_value["status"], "ABSENT")
        self.assertEqual(audit_entry.new_value["status"], "PRESENT")


class GradingEngineModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
        self.class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard Term Structure"
        )
        self.cat_type = AssessmentType.objects.create(
            school=self.school, name="CAT", code="CAT"
        )
        self.final_type = AssessmentType.objects.create(
            school=self.school, name="Final Exam", code="FINAL"
        )
        self.cat_component = AssessmentComponent.objects.create(
            structure=self.structure, assessment_type=self.cat_type,
            weight_percentage=Decimal("30.00"), max_marks=Decimal("30"),
        )
        self.final_component = AssessmentComponent.objects.create(
            structure=self.structure, assessment_type=self.final_type,
            weight_percentage=Decimal("70.00"), max_marks=Decimal("100"),
        )
        student_user = User.objects.create_user(
            username="s30", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM400",
            admission_date=datetime.date(2026, 1, 10),
        )

    def test_component_weight_must_be_between_0_and_100(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentComponent.objects.create(
                    structure=self.structure, assessment_type=self.cat_type,
                    weight_percentage=Decimal("150.00"),
                )

    def test_only_one_component_per_assessment_type_per_structure(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentComponent.objects.create(
                    structure=self.structure, assessment_type=self.cat_type,
                    weight_percentage=Decimal("10.00"),
                )

    def test_only_one_default_grading_scheme_per_school(self):
        scheme1 = GradingScheme.objects.create(
            school=self.school, name="Scheme A", is_default=True
        )
        scheme2 = GradingScheme.objects.create(
            school=self.school, name="Scheme B", is_default=True
        )
        scheme1.refresh_from_db()
        self.assertFalse(scheme1.is_default)
        self.assertTrue(scheme2.is_default)

    def test_grade_band_max_must_be_gte_min(self):
        scheme = GradingScheme.objects.create(school=self.school, name="Bad Scheme")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GradeBand.objects.create(
                    scheme=scheme, min_mark=Decimal("80"), max_mark=Decimal("50"),
                    grade="A", grade_point=Decimal("4.0"),
                )

    def test_get_grade_for_mark_resolves_correct_band(self):
        scheme = GradingScheme.objects.create(school=self.school, name="Standard")
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("80"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("70"), max_mark=Decimal("79.99"),
            grade="B", grade_point=Decimal("3.0"),
        )
        band = get_grade_for_mark(scheme, Decimal("85"))
        self.assertEqual(band.grade, "A")
        band2 = get_grade_for_mark(scheme, Decimal("75"))
        self.assertEqual(band2.grade, "B")
        self.assertIsNone(get_grade_for_mark(scheme, Decimal("40")))

    def test_validate_grade_bands_no_overlap_detects_overlap(self):
        scheme = GradingScheme.objects.create(school=self.school, name="Overlapping")
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("70"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("60"), max_mark=Decimal("75"),
            grade="B", grade_point=Decimal("3.0"),
        )
        errors = validate_grade_bands_no_overlap(scheme)
        self.assertEqual(len(errors), 1)

    def test_compute_weighted_average_uses_configured_weights_not_hardcoded(self):
        cat_assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.cat_component,
            title="CAT 1",
        )
        final_assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.final_component,
            title="Final Exam",
        )
        AssessmentMark.objects.create(
            assessment=cat_assessment, student=self.student, marks_obtained=Decimal("24"),
        )  # 24/30 -> 80% of 30% weight = 24.0
        AssessmentMark.objects.create(
            assessment=final_assessment, student=self.student, marks_obtained=Decimal("70"),
        )  # 70/100 -> 70% of 70% weight = 49.0

        result = compute_weighted_average(self.student, self.class_subject, self.term)
        self.assertEqual(result["weighted_total"], Decimal("73.00"))
        self.assertTrue(result["is_complete"])

    def test_compute_weighted_average_flags_incomplete_when_marks_missing(self):
        Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.cat_component,
            title="CAT 1",
        )
        Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.final_component,
            title="Final Exam",
        )
        result = compute_weighted_average(self.student, self.class_subject, self.term)
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["components_graded"], 0)

    def test_assessment_mark_unique_per_assessment_per_student(self):
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.cat_component,
            title="CAT 1",
        )
        AssessmentMark.objects.create(
            assessment=assessment, student=self.student, marks_obtained=Decimal("20"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentMark.objects.create(
                    assessment=assessment, student=self.student, marks_obtained=Decimal("25"),
                )

    def test_school_ranking_toggle_defaults_to_enabled(self):
        self.assertTrue(self.school.enable_position_ranking)


class ResultWorkflowTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(school=self.school, code="MATH", name="Mathematics")
        self.class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        final_type = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )
        self.assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="Final Exam",
        )
        self.teacher_user = User.objects.create_user(
            username="wfteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.class_teacher_user = User.objects.create_user(
            username="wfclassteacher", password="pass12345", role=User.Role.CLASS_TEACHER
        )
        self.academic_admin_user = User.objects.create_user(
            username="wfacadadmin", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )
        student_user = User.objects.create_user(
            username="wfstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM500",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.mark = AssessmentMark.objects.create(
            assessment=self.assessment, student=self.student, marks_obtained=Decimal("70"),
        )

    def _advance_to_verified(self):
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.SUBMITTED,
            actor=self.teacher_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.REVIEWED,
            actor=self.class_teacher_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.VERIFIED,
            actor=self.academic_admin_user,
        )

    def test_workflow_cannot_skip_stages(self):
        with self.assertRaises(ValueError):
            transition_assessment_workflow(
                assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
                actor=self.academic_admin_user,
            )

    def test_full_workflow_publishes_and_sets_is_published(self):
        self._advance_to_verified()
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
            actor=self.academic_admin_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.PUBLISHED,
            actor=self.academic_admin_user,
        )
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.workflow_status, Assessment.WorkflowStatus.PUBLISHED)
        self.assertTrue(self.assessment.is_published)

    def test_teacher_cannot_approve_own_submitted_assessment(self):
        self._advance_to_verified()
        with self.assertRaises(PermissionError):
            transition_assessment_workflow(
                assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
                actor=self.teacher_user,  # same user who submitted it
            )

    def test_reject_returns_assessment_to_draft(self):
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.SUBMITTED,
            actor=self.teacher_user,
        )
        reject_assessment(
            assessment=self.assessment, actor=self.academic_admin_user,
            reason="Marks look incomplete",
        )
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.workflow_status, Assessment.WorkflowStatus.DRAFT)

    def test_cannot_modify_published_mark_directly_without_amendment(self):
        """Spec §14 'Once published, prevent unrestricted modification' is
        enforced in AssessmentMark.save() — a direct edit must raise, not
        just be discouraged by convention."""
        self._advance_to_verified()
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
            actor=self.academic_admin_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.PUBLISHED,
            actor=self.academic_admin_user,
        )
        self.mark.marks_obtained = Decimal("99")
        with self.assertRaises(ValueError):
            self.mark.save()

        # The mark must be unchanged in the database.
        self.mark.refresh_from_db()
        self.assertEqual(self.mark.marks_obtained, Decimal("70"))

    def test_amendment_request_still_works_after_publish_lock(self):
        self._advance_to_verified()
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
            actor=self.academic_admin_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.PUBLISHED,
            actor=self.academic_admin_user,
        )
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Marking error found on recheck",
            proposed_mark=Decimal("75"), requested_by=self.teacher_user,
        )
        self.assertEqual(amendment.original_mark, Decimal("70"))
        self.assertEqual(amendment.status, ResultAmendmentRequest.Status.PENDING)

    def test_approving_amendment_updates_mark_and_logs_audit(self):
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Marking error found on recheck",
            proposed_mark=Decimal("75"), requested_by=self.teacher_user,
        )
        decide_result_amendment(
            amendment=amendment, approve=True, reviewed_by=self.academic_admin_user,
            comment="Confirmed with answer sheet",
        )
        self.mark.refresh_from_db()
        amendment.refresh_from_db()
        self.assertEqual(self.mark.marks_obtained, Decimal("75"))
        self.assertEqual(amendment.status, ResultAmendmentRequest.Status.APPROVED)

        audit_entry = AuditLog.objects.filter(
            target_model="AssessmentMark", target_object_id=str(self.mark.pk)
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.previous_value["marks_obtained"], "70")
        self.assertEqual(audit_entry.new_value["marks_obtained"], "75")

    def test_rejecting_amendment_leaves_mark_unchanged(self):
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Requesting re-mark",
            proposed_mark=Decimal("90"), requested_by=self.teacher_user,
        )
        decide_result_amendment(
            amendment=amendment, approve=False, reviewed_by=self.academic_admin_user,
            comment="Marking confirmed correct as-is",
        )
        self.mark.refresh_from_db()
        self.assertEqual(self.mark.marks_obtained, Decimal("70"))

    def test_cannot_decide_same_amendment_twice(self):
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Test", proposed_mark=Decimal("80"),
            requested_by=self.teacher_user,
        )
        decide_result_amendment(
            amendment=amendment, approve=True, reviewed_by=self.academic_admin_user,
        )
        with self.assertRaises(ValueError):
            decide_result_amendment(
                amendment=amendment, approve=True, reviewed_by=self.academic_admin_user,
            )