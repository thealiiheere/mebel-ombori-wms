"""Admin adds a new trade (kasb) from the Ishchilar page."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.inventory.models import MaterialCategory, MeasurementUnit
from apps.inventory.tests.factories import PASSWORD, make_admin, make_material, make_user
from apps.users.forms import trade_code_from
from apps.users.models import User, UserRole, WorkerRole, clear_role_cache, worker_role_codes


class WorkerRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()

    def setUp(self):
        # Each test's rows are rolled back; drop the per-process trade cache with them.
        clear_role_cache()
        self.addCleanup(clear_role_cache)

    def add_trade(self, name="Pardozchi"):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("users:worker_role_create"), {"name": name})
        self.client.logout()
        return response

    def test_original_trades_are_seeded(self):
        self.assertEqual(
            worker_role_codes(),
            [UserRole.ARRACHI, UserRole.KRASKACHI, UserRole.SHKURKACHI, UserRole.ROVERCHI, UserRole.SBORKACHI],
        )

    def test_ishchilar_page_has_the_new_trade_button(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("users:user_list"))
        self.assertContains(response, reverse("users:worker_role_create"))
        self.assertContains(response, "Yangi kasb")

    def test_admin_adds_a_trade(self):
        response = self.add_trade("Pardozchi")

        self.assertRedirects(response, reverse("users:user_list"), fetch_redirect_response=False)
        role = WorkerRole.objects.get(code="PARDOZCHI")
        self.assertEqual(role.name, "Pardozchi")
        self.assertEqual(worker_role_codes()[-1], "PARDOZCHI")

    def test_new_trade_can_register_and_sees_only_its_materials(self):
        self.add_trade("Pardozchi")

        self.assertContains(self.client.get(reverse("users:register")), "Pardozchi")
        self.client.post(reverse("users:register"), {
            "phone": "+998933334455", "full_name": "Jasur Aliyev", "roles": ["PARDOZCHI"],
            "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertEqual(User.objects.get(phone="+998933334455").roles, ["PARDOZCHI"])

        worker = make_user("pardozchi", ["PARDOZCHI"])
        make_material("Lak", MaterialCategory.LIQUID, MeasurementUnit.LITER, allowed_roles=["PARDOZCHI"], container_size=Decimal("5"))
        make_material("MDF 16 mm")  # sawyers and CNC operators only
        self.client.force_login(worker)
        home = self.client.get(reverse("inventory:home"))
        self.assertContains(home, "Lak")
        self.assertNotContains(home, "MDF 16 mm")

    def test_duplicate_name_is_rejected(self):
        self.add_trade("Pardozchi")
        response = self.add_trade("  pardozchi ")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkerRole.objects.filter(name__iexact="pardozchi").count(), 1)

    def test_codes_are_unique_and_never_admin(self):
        self.assertEqual(trade_code_from("Bo'yoqchi usta"), "BO_YOQCHI_USTA")
        self.assertEqual(trade_code_from("Admin"), "ADMIN_2")
        self.assertEqual(trade_code_from("Arrachi"), "ARRACHI_2")

    def test_workers_cannot_add_trades(self):
        self.client.force_login(make_user("arrachi", [UserRole.ARRACHI]))
        self.assertEqual(self.client.get(reverse("users:worker_role_create")).status_code, 403)
        self.assertEqual(self.client.post(reverse("users:worker_role_create"), {"name": "X"}).status_code, 403)
        self.assertFalse(WorkerRole.objects.filter(name="X").exists())
