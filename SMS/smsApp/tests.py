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
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    AuditLog,
    Book,
    BookCategory,
    BookCopy,
    Borrowing,
    Class,
    ClassSubject,
    Discussion,
    DiscussionReply,
    Enrollment,
    FeeCategory,
    FeeConcession,
    FeeStructure,
    FeeStructureItem,
    FinancialAdjustment,
    GradeBand,
    GradingScheme,
    Guardian,
    Invoice,
    InvoiceLineItem,
    LibrarySettings,
    LoginHistory,
    Payment,
    Program,
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    Receipt,
    Refund,
    ReportCard,
    ReportTemplate,
    ResultAmendmentRequest,
    School,
    Staff,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
    Transcript,
)
from .services import (
    apply_financial_adjustment,
    borrow_book,
    correct_attendance_record,
    compute_weighted_average,
    compute_student_account_summary,
    decide_refund,
    decide_result_amendment,
    generate_batch_reports,
    generate_invoice_for_student,
    generate_report_pdf,
    generate_transcript,
    get_grade_for_mark,
    grade_assignment_submission,
    grade_quiz_short_answer,
    mark_book_lost,
    pay_library_fine,
    record_payment,
    reject_assessment,
    request_refund,
    request_result_amendment,
    render_report_html,
    return_book,
    submit_assignment,
    submit_quiz_attempt,
    transition_assessment_workflow,
    validate_grade_bands_no_overlap,
    verify_transcript,
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


class ReportBookTests(TestCase):
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
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="Final Exam",
        )
        scheme = GradingScheme.objects.create(school=self.school, name="Standard", is_default=True)
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("80"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("0"), max_mark=Decimal("79.99"),
            grade="B", grade_point=Decimal("3.0"),
        )

        student_user = User.objects.create_user(
            username="rbstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM600",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )
        AssessmentMark.objects.create(
            assessment=assessment, student=self.student, marks_obtained=Decimal("85"),
        )

        self.admin_user = User.objects.create_superuser(
            username="rbadmin", email="rb@rb.com", password="pass12345"
        )
        self.template = ReportTemplate.objects.create(
            school=self.school, name="Standard Report", is_default=True,
        )

    def test_render_report_html_includes_subject_and_grade(self):
        card = ReportCard.objects.create(
            student=self.student, term=self.term, template=self.template,
        )
        html = render_report_html(report_card=card)
        self.assertIn("Mathematics", html)
        self.assertIn("85.00", html)
        self.assertIn("A", html)

    def test_generate_report_pdf_creates_downloadable_file(self):
        card = ReportCard.objects.create(
            student=self.student, term=self.term, template=self.template,
        )
        generate_report_pdf(report_card=card, generated_by=self.admin_user)
        card.refresh_from_db()
        self.assertTrue(card.pdf_file.name)
        self.assertIsNotNone(card.generated_at)
        card.pdf_file.open("rb")
        header = card.pdf_file.read(4)
        card.pdf_file.close()
        self.assertEqual(header, b"%PDF")

    def test_batch_generate_creates_card_per_active_student_in_class(self):
        second_user = User.objects.create_user(
            username="rbstudent2", password="pass12345", role=User.Role.STUDENT
        )
        Student.objects.create(
            user=second_user, school=self.school, admission_number="ADM601",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        cards = generate_batch_reports(
            class_group=self.class_group, term=self.term, template=self.template,
            generated_by=self.admin_user,
        )
        self.assertEqual(len(cards), 2)
        self.assertEqual(ReportCard.objects.filter(term=self.term).count(), 2)

    def test_only_one_default_report_template_per_school(self):
        second_template = ReportTemplate.objects.create(
            school=self.school, name="Alt Report", is_default=True,
        )
        self.template.refresh_from_db()
        self.assertFalse(self.template.is_default)
        self.assertTrue(second_template.is_default)

    def test_report_card_unique_per_student_term_template(self):
        ReportCard.objects.create(
            student=self.student, term=self.term, template=self.template,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReportCard.objects.create(
                    student=self.student, term=self.term, template=self.template,
                )


class TranscriptTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(
            school=self.school, name="BSc CS", code="BSCCS",
            program_type=Program.ProgramType.UNIVERSITY,
        )
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Year 1"
        )
        self.subject_math = Subject.objects.create(
            school=self.school, code="MATH101", name="Calculus I", credit_hours=Decimal("4.0")
        )
        self.subject_cs = Subject.objects.create(
            school=self.school, code="CS101", name="Intro to CS", credit_hours=Decimal("3.0")
        )
        self.class_subject_math = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject_math
        )
        self.class_subject_cs = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject_cs
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term1 = Term.objects.create(
            academic_year=self.academic_year, name="Semester 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.term2 = Term.objects.create(
            academic_year=self.academic_year, name="Semester 2", term_number=2,
            start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 8, 1),
        )
        self.scheme = GradingScheme.objects.create(
            school=self.school, name="4.0 Scale", is_default=True
        )
        GradeBand.objects.create(
            scheme=self.scheme, min_mark=Decimal("90"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=self.scheme, min_mark=Decimal("80"), max_mark=Decimal("89.99"),
            grade="B", grade_point=Decimal("3.0"),
        )
        GradeBand.objects.create(
            scheme=self.scheme, min_mark=Decimal("0"), max_mark=Decimal("79.99"),
            grade="C", grade_point=Decimal("2.0"),
        )

        student_user = User.objects.create_user(
            username="tstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM700",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject_math,
            academic_year=self.academic_year,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject_cs,
            academic_year=self.academic_year,
        )

        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term1, name="Standard"
        )
        final_type = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )

        # Term 1: Calculus I (credit=4) scores 95 -> grade A (4.0);
        # published so it counts toward the transcript.
        math_assessment_t1 = Assessment.objects.create(
            class_subject=self.class_subject_math, term=self.term1, component=component,
            title="Final Exam", workflow_status=Assessment.WorkflowStatus.PUBLISHED,
        )
        AssessmentMark.objects.create(
            assessment=math_assessment_t1, student=self.student, marks_obtained=Decimal("95"),
        )

        # Term 2: Intro to CS (credit=3) scores 85 -> grade B (3.0).
        component2 = AssessmentComponent.objects.create(
            structure=AssessmentStructure.objects.create(
                school=self.school, term=self.term2, name="Standard"
            ),
            assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )
        cs_assessment_t2 = Assessment.objects.create(
            class_subject=self.class_subject_cs, term=self.term2, component=component2,
            title="Final Exam", workflow_status=Assessment.WorkflowStatus.PUBLISHED,
        )
        AssessmentMark.objects.create(
            assessment=cs_assessment_t2, student=self.student, marks_obtained=Decimal("85"),
        )

        self.admin_user = User.objects.create_superuser(
            username="transcriptadmin", email="ta@ta.com", password="pass12345"
        )

    def test_generate_transcript_snapshots_only_published_entries(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        self.assertEqual(transcript.entries.count(), 2)
        subject_names = set(transcript.entries.values_list("subject_name", flat=True))
        self.assertEqual(subject_names, {"Calculus I", "Intro to CS"})

    def test_transcript_excludes_unpublished_assessments(self):
        structure = AssessmentStructure.objects.get(term=self.term1)
        draft_component = AssessmentComponent.objects.create(
            structure=structure,
            assessment_type=AssessmentType.objects.create(
                school=self.school, name="CAT", code="CAT1"
            ),
            weight_percentage=Decimal("0.00"), max_marks=Decimal("100"),
        )
        draft_assessment = Assessment.objects.create(
            class_subject=self.class_subject_math, term=self.term1, component=draft_component,
            title="Draft CAT", workflow_status=Assessment.WorkflowStatus.DRAFT,
        )
        AssessmentMark.objects.create(
            assessment=draft_assessment, student=self.student, marks_obtained=Decimal("40"),
        )
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        # Still only 2 entries: the draft assessment's term/class_subject
        # combination must not surface as a separate published entry.
        self.assertEqual(transcript.entries.count(), 2)

    def test_cgpa_is_credit_hour_weighted(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        # (4.0*4 + 3.0*3) / (4+3) = 25/7 = 3.5714... -> 3.57
        self.assertEqual(transcript.cgpa, Decimal("3.57"))

    def test_transcript_pdf_is_generated_and_downloadable(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        self.assertTrue(transcript.pdf_file.name)
        transcript.pdf_file.open("rb")
        header = transcript.pdf_file.read(4)
        transcript.pdf_file.close()
        self.assertEqual(header, b"%PDF")

    def test_verification_code_is_unique_and_verifiable(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        result = verify_transcript(transcript.verification_code)
        self.assertTrue(result["valid"])
        self.assertEqual(result["admission_number"], "ADM700")

    def test_invalid_verification_code_returns_not_valid(self):
        import uuid
        result = verify_transcript(uuid.uuid4())
        self.assertFalse(result["valid"])

    def test_academic_status_and_graduation_status_snapshotted(self):
        self.student.status = Student.Status.GRADUATED
        self.student.save()
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        self.assertEqual(transcript.academic_status, Student.Status.GRADUATED)
        self.assertEqual(transcript.graduation_status, Transcript.GraduationStatus.GRADUATED)


class LMSTests(TestCase):
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
        teacher_user = User.objects.create_user(
            username="lmsteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.teacher = Staff.objects.create(
            user=teacher_user, school=self.school, staff_id="STF900",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        student_user = User.objects.create_user(
            username="lmsstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM800",
            admission_date=datetime.date(2026, 1, 10),
        )

    # --- Assignments ---

    def test_first_submission_always_allowed(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 1",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            allow_resubmission=False,
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="My essay"
        )
        self.assertEqual(submission.attempt_number, 1)
        self.assertEqual(submission.status, AssignmentSubmission.Status.SUBMITTED)

    def test_resubmission_blocked_when_not_allowed(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 2",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            allow_resubmission=False,
        )
        submit_assignment(assignment=assignment, student=self.student, submitted_text="v1")
        with self.assertRaises(ValueError):
            submit_assignment(assignment=assignment, student=self.student, submitted_text="v2")

    def test_resubmission_allowed_increments_attempt_and_clears_grade(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 3",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            allow_resubmission=True,
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="v1"
        )
        grade_assignment_submission(
            submission=submission, marks_obtained=Decimal("70"), feedback="Good start",
            graded_by=self.teacher,
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="v2"
        )
        self.assertEqual(submission.attempt_number, 2)
        self.assertEqual(submission.status, AssignmentSubmission.Status.RESUBMITTED)
        self.assertIsNone(submission.marks_obtained)

    def test_late_submission_flagged(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 4",
            deadline=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="Late work"
        )
        self.assertTrue(submission.is_late)

    def test_grade_cannot_exceed_max_marks(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 5",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            max_marks=Decimal("50"),
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="v1"
        )
        with self.assertRaises(ValueError):
            grade_assignment_submission(
                submission=submission, marks_obtained=Decimal("75"), feedback="",
                graded_by=self.teacher,
            )

    # --- Quizzes ---

    def _make_quiz_with_questions(self):
        quiz = Quiz.objects.create(
            class_subject=self.class_subject, term=self.term, title="Quiz 1"
        )
        mcq = QuizQuestion.objects.create(
            quiz=quiz, question_text="2 + 2 = ?",
            question_type=QuizQuestion.QuestionType.MULTIPLE_CHOICE, marks=Decimal("2"),
        )
        correct_opt = QuizOption.objects.create(question=mcq, option_text="4", is_correct=True)
        QuizOption.objects.create(question=mcq, option_text="5", is_correct=False)

        multi = QuizQuestion.objects.create(
            quiz=quiz, question_text="Select all primes",
            question_type=QuizQuestion.QuestionType.MULTIPLE_ANSWER, marks=Decimal("3"),
        )
        prime2 = QuizOption.objects.create(question=multi, option_text="2", is_correct=True)
        prime3 = QuizOption.objects.create(question=multi, option_text="3", is_correct=True)
        QuizOption.objects.create(question=multi, option_text="4", is_correct=False)

        short = QuizQuestion.objects.create(
            quiz=quiz, question_text="Explain your reasoning",
            question_type=QuizQuestion.QuestionType.SHORT_ANSWER, marks=Decimal("5"),
        )
        return quiz, mcq, correct_opt, multi, prime2, prime3, short

    def test_mcq_auto_grades_correctly(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                multi.pk: {"option_ids": [prime2.pk, prime3.pk]},
                short.pk: {"text": "Because math."},
            },
        )
        attempt.refresh_from_db()
        # MCQ (2) + multi-answer exact match (3) = 5; short answer ungraded.
        self.assertEqual(attempt.auto_score, Decimal("5"))
        self.assertFalse(attempt.is_fully_graded)

    def test_multiple_answer_requires_exact_set_match(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                # Only one of two correct primes selected -> incorrect, 0 marks.
                multi.pk: {"option_ids": [prime2.pk]},
                short.pk: {"text": "..."},
            },
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.auto_score, Decimal("2"))  # only the MCQ mark

    def test_short_answer_requires_manual_grading_to_finalize(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                multi.pk: {"option_ids": [prime2.pk, prime3.pk]},
                short.pk: {"text": "Because math."},
            },
        )
        attempt.refresh_from_db()
        self.assertFalse(attempt.is_fully_graded)

        short_answer = attempt.answers.get(question=short)
        grade_quiz_short_answer(answer=short_answer, marks_awarded=Decimal("4"))
        attempt.refresh_from_db()
        self.assertTrue(attempt.is_fully_graded)
        self.assertEqual(attempt.manual_score, Decimal("4"))
        self.assertEqual(attempt.total_score, Decimal("9"))  # 5 auto + 4 manual

    def test_short_answer_grade_cannot_exceed_question_marks(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                multi.pk: {"option_ids": [prime2.pk, prime3.pk]},
                short.pk: {"text": "..."},
            },
        )
        short_answer = attempt.answers.get(question=short)
        with self.assertRaises(ValueError):
            grade_quiz_short_answer(answer=short_answer, marks_awarded=Decimal("10"))

    def test_only_one_attempt_number_per_quiz_student(self):
        quiz, *_ = self._make_quiz_with_questions()
        QuizAttempt.objects.create(quiz=quiz, student=self.student, attempt_number=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuizAttempt.objects.create(quiz=quiz, student=self.student, attempt_number=1)

    # --- Discussions ---

    def test_discussion_and_reply_creation(self):
        discussion = Discussion.objects.create(
            class_subject=self.class_subject, term=self.term,
            thread_type=Discussion.ThreadType.ANNOUNCEMENT,
            title="Midterm postponed", created_by=self.teacher.user,
        )
        DiscussionReply.objects.create(
            discussion=discussion, author=self.student.user, content="Thanks for the update!"
        )
        self.assertEqual(discussion.replies.count(), 1)


class FinanceTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.tuition = FeeCategory.objects.create(
            school=self.school, name="Tuition", code="TUITION"
        )
        self.transport = FeeCategory.objects.create(
            school=self.school, name="Transport", code="TRANSPORT"
        )
        self.structure = FeeStructure.objects.create(
            school=self.school, academic_year=self.academic_year, term=self.term,
            name="Term 1 Fees",
        )
        FeeStructureItem.objects.create(
            structure=self.structure, category=self.tuition, amount=Decimal("50000")
        )
        FeeStructureItem.objects.create(
            structure=self.structure, category=self.transport, amount=Decimal("10000")
        )

        student_user = User.objects.create_user(
            username="financestudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM900",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.finance_admin = User.objects.create_user(
            username="financeadmin", password="pass12345", role=User.Role.FINANCE_ADMIN
        )

    def test_generate_invoice_sums_structure_items(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(invoice.total_amount, Decimal("60000"))
        self.assertEqual(invoice.line_items.count(), 2)
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertTrue(invoice.invoice_number.startswith("INV-"))

    def test_percentage_concession_reduces_invoice_total(self):
        FeeConcession.objects.create(
            student=self.student, academic_year=self.academic_year,
            concession_type=FeeConcession.ConcessionType.SCHOLARSHIP,
            percentage=Decimal("20.00"),
        )
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        # 60000 - 20% = 48000
        self.assertEqual(invoice.total_amount, Decimal("48000.00"))
        scholarship_line = invoice.line_items.get(
            line_type=FeeConcession.ConcessionType.SCHOLARSHIP
        )
        self.assertEqual(scholarship_line.amount, Decimal("12000.00"))

    def test_fixed_amount_concession_applied(self):
        FeeConcession.objects.create(
            student=self.student, academic_year=self.academic_year,
            concession_type=FeeConcession.ConcessionType.WAIVER,
            fixed_amount=Decimal("5000"),
        )
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(invoice.total_amount, Decimal("55000"))

    def test_concession_cannot_push_invoice_negative(self):
        FeeConcession.objects.create(
            student=self.student, academic_year=self.academic_year,
            concession_type=FeeConcession.ConcessionType.WAIVER,
            fixed_amount=Decimal("999999"),
        )
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(invoice.total_amount, Decimal("0"))

    def test_partial_payment_sets_invoice_partially_paid_and_generates_receipt(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("20000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertTrue(hasattr(payment, "receipt"))
        self.assertTrue(payment.receipt.receipt_number.startswith("RCT-"))

    def test_full_payment_marks_invoice_paid(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.MPESA,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin, gateway_reference="QGH7XYZ123",
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    def test_overpayment_is_rejected(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        with self.assertRaises(ValueError):
            record_payment(
                invoice=invoice, amount=Decimal("70000"), payment_method=Payment.Method.CASH,
                payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
                received_by=self.finance_admin,
            )

    def test_refund_approval_reduces_invoice_paid_status(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        refund = request_refund(
            payment=payment, amount=Decimal("10000"), reason="Overcharged transport fee",
            requested_by=self.finance_admin,
        )
        decide_refund(refund=refund, approve=True, decided_by=self.finance_admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)

    def test_cannot_decide_same_refund_twice(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        refund = request_refund(
            payment=payment, amount=Decimal("5000"), reason="Test", requested_by=self.finance_admin,
        )
        decide_refund(refund=refund, approve=True, decided_by=self.finance_admin)
        with self.assertRaises(ValueError):
            decide_refund(refund=refund, approve=True, decided_by=self.finance_admin)

    def test_financial_adjustment_changes_invoice_total_and_status(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        apply_financial_adjustment(
            invoice=invoice, adjustment_type=FinancialAdjustment.AdjustmentType.CORRECTION,
            amount=Decimal("5000"), reason="Missed a late library fine",
            created_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, Decimal("65000"))
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)

    def test_account_summary_reports_outstanding_and_arrears(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2020, 1, 1),  # overdue
        )
        record_payment(
            invoice=invoice, amount=Decimal("10000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2020, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        summary = compute_student_account_summary(student=self.student)
        self.assertEqual(summary["total_billed"], Decimal("60000"))
        self.assertEqual(summary["total_paid"], Decimal("10000"))
        self.assertEqual(summary["outstanding_balance"], Decimal("50000"))
        self.assertEqual(summary["arrears"], Decimal("60000"))  # still not PAID and overdue

    def test_invoice_line_item_amounts_are_non_negative(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    invoice_number="INV-TEST1", student=self.student, school=self.school,
                    academic_year=self.academic_year, term=self.term,
                    fee_structure=self.structure, total_amount=Decimal("100"),
                    issue_date=datetime.date(2026, 1, 1), due_date=datetime.date(2026, 2, 1),
                )
                InvoiceLineItem.objects.create(
                    invoice=invoice, line_type="FEE", amount=Decimal("-100"),
                )


class LibraryTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        LibrarySettings.objects.create(
            school=self.school, loan_period_days=14, max_books_per_student=2,
            fine_per_day=Decimal("10.00"),
        )
        self.category = BookCategory.objects.create(school=self.school, name="Fiction")
        self.book = Book.objects.create(
            school=self.school, title="The Hobbit", isbn="9780618968633",
            category=self.category,
        )
        self.copy1 = BookCopy.objects.create(book=self.book, accession_number="ACC001")
        self.copy2 = BookCopy.objects.create(book=self.book, accession_number="ACC002")

        student_user = User.objects.create_user(
            username="librarystudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM950",
            admission_date=datetime.date(2026, 1, 10),
        )
        librarian_user = User.objects.create_user(
            username="librarian1", password="pass12345", role=User.Role.LIBRARIAN
        )
        self.librarian = Staff.objects.create(
            user=librarian_user, school=self.school, staff_id="STF950",
            job_title="Librarian", date_hired=datetime.date(2024, 1, 1),
        )

    def test_borrow_book_marks_copy_borrowed(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        self.copy1.refresh_from_db()
        self.assertEqual(self.copy1.status, BookCopy.Status.BORROWED)
        self.assertEqual(borrowing.due_date - borrowing.borrowed_date, datetime.timedelta(days=14))

    def test_cannot_borrow_unavailable_copy(self):
        borrow_book(book_copy=self.copy1, student=self.student, issued_by=self.librarian)
        student_user2 = User.objects.create_user(
            username="librarystudent2", password="pass12345", role=User.Role.STUDENT
        )
        student2 = Student.objects.create(
            user=student_user2, school=self.school, admission_number="ADM951",
            admission_date=datetime.date(2026, 1, 10),
        )
        with self.assertRaises(ValueError):
            borrow_book(book_copy=self.copy1, student=student2, issued_by=self.librarian)

    def test_borrower_must_be_exactly_one_of_student_or_staff(self):
        with self.assertRaises(ValueError):
            borrow_book(book_copy=self.copy1, issued_by=self.librarian)
        with self.assertRaises(ValueError):
            borrow_book(
                book_copy=self.copy1, student=self.student, staff=self.librarian,
                issued_by=self.librarian,
            )

    def test_student_book_limit_enforced(self):
        book2 = Book.objects.create(school=self.school, title="The Two Towers")
        copy_b2 = BookCopy.objects.create(book=book2, accession_number="ACC010")
        book3 = Book.objects.create(school=self.school, title="Return of the King")
        copy_b3 = BookCopy.objects.create(book=book3, accession_number="ACC011")

        borrow_book(book_copy=self.copy1, student=self.student, issued_by=self.librarian)
        borrow_book(book_copy=copy_b2, student=self.student, issued_by=self.librarian)
        # max_books_per_student=2 for this school -> third borrow must fail.
        with self.assertRaises(ValueError):
            borrow_book(book_copy=copy_b3, student=self.student, issued_by=self.librarian)

    def test_return_on_time_has_no_fine(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        return_book(borrowing=borrowing, returned_to=self.librarian)
        borrowing.refresh_from_db()
        self.assertEqual(borrowing.status, Borrowing.Status.RETURNED)
        self.assertEqual(borrowing.fine_amount, Decimal("0.00"))
        self.copy1.refresh_from_db()
        self.assertEqual(self.copy1.status, BookCopy.Status.AVAILABLE)

    def test_late_return_computes_fine(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        # Force the due date into the past to simulate lateness.
        borrowing.due_date = datetime.date.today() - datetime.timedelta(days=3)
        borrowing.save(update_fields=["due_date"])

        return_book(borrowing=borrowing, returned_to=self.librarian)
        borrowing.refresh_from_db()
        # 3 days late * 10.00/day = 30.00
        self.assertEqual(borrowing.fine_amount, Decimal("30.00"))

    def test_cannot_return_already_returned_borrowing(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        return_book(borrowing=borrowing, returned_to=self.librarian)
        with self.assertRaises(ValueError):
            return_book(borrowing=borrowing, returned_to=self.librarian)

    def test_mark_book_lost_updates_copy_status(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        mark_book_lost(borrowing=borrowing, marked_by=self.librarian)
        borrowing.refresh_from_db()
        self.copy1.refresh_from_db()
        self.assertEqual(borrowing.status, Borrowing.Status.LOST)
        self.assertEqual(self.copy1.status, BookCopy.Status.LOST)

    def test_pay_fine_requires_full_amount(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        borrowing.due_date = datetime.date.today() - datetime.timedelta(days=2)
        borrowing.save(update_fields=["due_date"])
        return_book(borrowing=borrowing, returned_to=self.librarian)
        borrowing.refresh_from_db()

        with self.assertRaises(ValueError):
            pay_library_fine(
                borrowing=borrowing, amount_paid=Decimal("5.00"), received_by=self.librarian,
            )
        pay_library_fine(
            borrowing=borrowing, amount_paid=borrowing.fine_amount, received_by=self.librarian,
        )
        borrowing.refresh_from_db()
        self.assertTrue(borrowing.fine_paid)

    def test_staff_can_also_borrow(self):
        borrowing = borrow_book(
            book_copy=self.copy2, staff=self.librarian, issued_by=self.librarian,
        )
        self.assertEqual(borrowing.staff, self.librarian)
        self.assertIsNone(borrowing.student)

    def test_active_borrowing_unique_per_copy_at_db_level(self):
        borrow_book(book_copy=self.copy1, student=self.student, issued_by=self.librarian)
        student_user2 = User.objects.create_user(
            username="librarystudent3", password="pass12345", role=User.Role.STUDENT
        )
        student2 = Student.objects.create(
            user=student_user2, school=self.school, admission_number="ADM952",
            admission_date=datetime.date(2026, 1, 10),
        )
        # Bypass the service-layer status check to confirm the DB itself
        # enforces at most one active (unreturned) borrowing per copy.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Borrowing.objects.create(
                    book_copy=self.copy1, student=student2,
                    borrowed_date=datetime.date.today(),
                    due_date=datetime.date.today() + datetime.timedelta(days=14),
                )