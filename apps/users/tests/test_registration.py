"""1. Worker self-registration and the admin approval flow (HTML and API)."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.inventory.tests.factories import PASSWORD, make_admin, make_user
from apps.users.models import User, UserRole


def registration_data(**overrides):
    data = {
        "phone": "+998 90 123 45 67",
        "full_name": "Bobur Karimov",
        "roles": [UserRole.ARRACHI, UserRole.ROVERCHI],
        "password1": PASSWORD,
        "password2": PASSWORD,
    }
    data.update(overrides)
    return data


class SelfRegistrationTests(TestCase):
    def test_registration_creates_pending_worker_with_chosen_roles(self):
        response = self.client.post(reverse("users:register"), registration_data())

        self.assertRedirects(response, reverse("users:register_done"))
        user = User.objects.get(phone="+998901234567")
        self.assertFalse(user.is_approved)
        self.assertTrue(user.is_active)
        self.assertEqual(user.full_name, "Bobur Karimov")
        self.assertEqual(user.roles, [UserRole.ARRACHI, UserRole.ROVERCHI])
        self.assertTrue(user.check_password(PASSWORD))

    def test_pending_message_is_shown_after_registration(self):
        response = self.client.post(reverse("users:register"), registration_data(), follow=True)
        self.assertContains(response, "Tasdiq kutilmoqda")

    def test_at_least_one_role_is_required(self):
        response = self.client.post(reverse("users:register"), registration_data(roles=[]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(phone="+998901234567").exists())
        self.assertFormError(response.context["form"], "roles", "Kamida bitta kasbni tanlang.")

    def test_workers_cannot_grant_themselves_the_admin_role(self):
        response = self.client.post(reverse("users:register"), registration_data(roles=[UserRole.ADMIN]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(phone="+998901234567").exists())

    def test_registration_does_not_log_the_worker_in(self):
        self.client.post(reverse("users:register"), registration_data())
        response = self.client.get(reverse("inventory:home"))
        self.assertRedirects(response, f"{reverse('users:login')}?next=/")


class LoginGateTests(TestCase):
    def test_pending_worker_cannot_log_in_and_sees_pending_message(self):
        make_user("pending", [UserRole.KRASKACHI], approved=False)
        response = self.client.post(reverse("users:login"), {"username": "pending", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hali tasdiqlanmagan")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_password_for_pending_worker_gets_the_generic_error(self):
        make_user("pending", [UserRole.KRASKACHI], approved=False)
        response = self.client.post(reverse("users:login"), {"username": "pending", "password": "wrong-one"})
        self.assertContains(response, "Login yoki parol noto")
        self.assertNotContains(response, "hali tasdiqlanmagan")

    def test_approved_worker_can_log_in(self):
        make_user("ready", [UserRole.KRASKACHI], approved=True)
        response = self.client.post(reverse("users:login"), {"username": "ready", "password": PASSWORD})
        self.assertRedirects(response, reverse("inventory:home"))

    def test_revoking_approval_ends_the_existing_session(self):
        worker = make_user("ready", [UserRole.KRASKACHI])
        self.client.force_login(worker)
        self.assertEqual(self.client.get(reverse("inventory:home")).status_code, 200)

        User.objects.filter(pk=worker.pk).update(is_approved=False)

        response = self.client.get(reverse("inventory:home"))
        self.assertRedirects(response, f"{reverse('users:login')}?next=/")

    def test_superuser_is_created_approved_with_admin_role(self):
        admin = get_user_model().objects.create_superuser("root", password=PASSWORD)
        self.assertTrue(admin.is_approved)
        self.assertEqual(admin.roles, [UserRole.ADMIN])
        self.assertTrue(admin.is_warehouse_admin)


class ApprovalWorkflowTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.pending = make_user("otabek", [UserRole.SHKURKACHI], approved=False, full_name="Otabek Nazarov")

    def test_pending_list_is_admin_only(self):
        url = reverse("users:pending_users")
        self.assertRedirects(self.client.get(url), f"{reverse('users:login')}?next={url}")

        self.client.force_login(make_user("worker", [UserRole.ARRACHI]))
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Otabek Nazarov")

    def test_admin_can_adjust_roles_and_approve(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("users:pending_user_review", args=[self.pending.pk]),
            {"full_name": "Otabek Nazarov", "roles": [UserRole.SHKURKACHI, UserRole.SBORKACHI]},
        )
        self.assertRedirects(response, reverse("users:pending_users"))

        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_approved)
        self.assertEqual(self.pending.roles, [UserRole.SHKURKACHI, UserRole.SBORKACHI])
        self.assertEqual(self.pending.approved_by, self.admin)
        self.assertIsNotNone(self.pending.approved_at)

        # ...and the worker can now log in
        self.client.logout()
        response = self.client.post(reverse("users:login"), {"username": "otabek", "password": PASSWORD})
        self.assertRedirects(response, reverse("inventory:home"))

    def test_approval_needs_at_least_one_role(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("users:pending_user_review", args=[self.pending.pk]), {"full_name": "x", "roles": []}
        )
        self.assertEqual(response.status_code, 200)
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.is_approved)

    def test_admin_can_reject_a_request(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("users:pending_user_reject", args=[self.pending.pk]))
        self.assertRedirects(response, reverse("users:pending_users"))
        self.assertFalse(User.objects.filter(pk=self.pending.pk).exists())

    def test_reject_only_accepts_post_and_only_pending_users(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("users:pending_user_reject", args=[self.pending.pk])).status_code, 405)
        approved = make_user("veteran", [UserRole.ARRACHI])
        self.assertEqual(self.client.post(reverse("users:pending_user_reject", args=[approved.pk])).status_code, 404)
        self.assertTrue(User.objects.filter(pk=approved.pk).exists())

    def test_admin_cannot_remove_their_own_admin_role(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("users:user_edit", args=[self.admin.pk]),
            {"full_name": "Boss", "roles": [UserRole.ARRACHI], "is_approved": "on", "is_active": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertIn(UserRole.ADMIN, self.admin.roles)


class RegistrationApiTests(TestCase):
    def setUp(self):
        cache.clear()  # reset the registration throttle

    def test_api_registration_creates_pending_worker(self):
        response = self.client.post(
            "/api/auth/register/",
            {"phone": "+998912223344", "full_name": "Dilshod Rahimov", "password": PASSWORD, "roles": [UserRole.KRASKACHI]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(phone="+998912223344")
        self.assertFalse(user.is_approved)
        self.assertEqual(user.roles, [UserRole.KRASKACHI])

    def test_api_registration_rejects_the_admin_role(self):
        response = self.client.post(
            "/api/auth/register/",
            {"phone": "+998912223355", "full_name": "Sly", "password": PASSWORD, "roles": [UserRole.ADMIN]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("roles", response.json())
        self.assertFalse(User.objects.filter(phone="+998912223355").exists())

    def test_api_registration_rejects_a_weak_password(self):
        response = self.client.post(
            "/api/auth/register/",
            {"phone": "+998912223355", "full_name": "Sly", "password": "12345678", "roles": [UserRole.ARRACHI]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json())
        self.assertFalse(User.objects.filter(phone="+998912223355").exists())

    def test_admin_approves_through_api_and_workers_cannot(self):
        admin = make_admin()
        pending = make_user("jasur", [UserRole.SBORKACHI], approved=False)

        self.client.force_login(make_user("worker", [UserRole.ARRACHI]))
        self.assertEqual(self.client.get("/api/pending-users/").status_code, 403)

        self.client.force_login(admin)
        listing = self.client.get("/api/pending-users/").json()
        self.assertEqual([row["username"] for row in listing["results"]], ["jasur"])

        response = self.client.post(f"/api/pending-users/{pending.pk}/approve/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        pending.refresh_from_db()
        self.assertTrue(pending.is_approved)

    def test_unapproved_session_cannot_use_the_api(self):
        pending = make_user("jasur", [UserRole.SBORKACHI], approved=False)
        self.client.force_login(pending)
        self.assertEqual(self.client.get("/api/whole-materials/").status_code, 403)
