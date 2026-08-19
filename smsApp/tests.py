# Path: SMS/smsApp/tests.py
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class UserModelTests(TestCase):
    def test_create_user_with_email(self):
        user = User.objects.create_user(email="teacher@example.com", password="testpass123")
        self.assertEqual(user.email, "teacher@example.com")
        self.assertTrue(user.check_password("testpass123"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_create_user_without_email_raises(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="testpass123")

    def test_create_superuser(self):
        admin = User.objects.create_superuser(email="admin@example.com", password="adminpass123")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_email_is_normalized(self):
        user = User.objects.create_user(email="Teacher@EXAMPLE.com", password="testpass123")
        self.assertEqual(user.email, "Teacher@example.com")

    def test_email_uniqueness_enforced(self):
        User.objects.create_user(email="dup@example.com", password="testpass123")
        with self.assertRaises(Exception):
            User.objects.create_user(email="dup@example.com", password="anotherpass123")

    def test_str_returns_email(self):
        user = User.objects.create_user(email="teacher@example.com", password="testpass123")
        self.assertEqual(str(user), "teacher@example.com")


class UserAdminAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(email="admin@example.com", password="adminpass123")

    def test_superuser_can_log_into_admin(self):
        self.client.login(email="admin@example.com", password="adminpass123")
        response = self.client.get(reverse("admin:smsApp_user_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_redirected_from_admin(self):
        response = self.client.get(reverse("admin:smsApp_user_changelist"))
        self.assertEqual(response.status_code, 302)