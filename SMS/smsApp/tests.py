# Absolute path: SMS/smsApp/tests.py
import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import (
    AcademicYear,
    AuditLog,
    Class,
    ClassSubject,
    Enrollment,
    Guardian,
    LoginHistory,
    Program,
    School,
    Staff,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
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