"""5. Phone-number login, the readable password an admin reads out, and the
administrator being barred from taking stock off the shelf."""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.inventory import services
from apps.inventory.models import MaterialCategory, MeasurementUnit
from apps.inventory.tests.factories import (
    PASSWORD,
    make_admin,
    make_material,
    make_user,
    stock_up,
)
from apps.users.models import User, UserRole, normalize_phone, phone_display


class PhoneNormalisationTests(TestCase):
    def test_every_way_of_writing_a_number_lands_on_one_form(self):
        for typed in (
            "901234567",
            "90 123 45 67",
            "+998901234567",
            "+998 90 123 45 67",
            "998901234567",
            "0901234567",
            "(90) 123-45-67",
        ):
            with self.subTest(typed=typed):
                self.assertEqual(normalize_phone(typed), "+998901234567")

    def test_a_number_of_the_wrong_length_is_refused(self):
        for typed in ("", "123", "9012345678", "+1 555 010 2030", "abc"):
            with self.subTest(typed=typed):
                with self.assertRaises(ValidationError):
                    normalize_phone(typed)

    def test_display_form_is_grouped_for_reading_aloud(self):
        self.assertEqual(phone_display("+998901234567"), "+998 90 123 45 67")


class PhoneLoginTests(TestCase):
    def setUp(self):
        self.worker = make_user("bobur", [UserRole.ARRACHI], phone="+998901234567")

    def test_worker_logs_in_with_the_phone_number(self):
        response = self.client.post(
            reverse("users:login"), {"username": "+998901234567", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("inventory:home"), fetch_redirect_response=False)

    def test_the_number_may_be_typed_with_spaces_or_without_the_prefix(self):
        for typed in ("+998 90 123 45 67", "90 123 45 67", "901234567"):
            with self.subTest(typed=typed):
                self.client.logout()
                response = self.client.post(
                    reverse("users:login"), {"username": typed, "password": PASSWORD}
                )
                self.assertRedirects(
                    response, reverse("inventory:home"), fetch_redirect_response=False
                )

    def test_a_wrong_password_is_still_refused(self):
        response = self.client.post(
            reverse("users:login"), {"username": "901234567", "password": "not-the-one"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_page_asks_for_a_phone_number(self):
        response = self.client.get(reverse("users:login"))
        self.assertContains(response, "Telefon raqami")
        self.assertContains(response, 'inputmode="tel"')

    def test_registration_stores_the_phone_as_the_login_id(self):
        self.client.post(
            reverse("users:register"),
            {
                "phone": "90 765 43 21",
                "full_name": "Sardor Aliyev",
                "roles": [UserRole.ARRACHI],
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        user = User.objects.get(phone="+998907654321")
        self.assertEqual(user.username, "+998907654321")
        self.assertEqual(user.login_id, "+998 90 765 43 21")

    def test_the_same_number_cannot_register_twice(self):
        response = self.client.post(
            reverse("users:register"),
            {
                "phone": "+998 90 123 45 67",  # already taken in setUp
                "full_name": "Boshqa odam",
                "roles": [UserRole.ARRACHI],
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "phone", "Bu raqam allaqachon ro'yxatdan o'tgan.")
        self.assertEqual(User.objects.filter(phone="+998901234567").count(), 1)


class ReadablePasswordTests(TestCase):
    """The admin reads a worker's password out over the phone, so it is stored as typed."""

    def test_setting_a_password_keeps_the_readable_copy_in_step(self):
        user = make_user("bobur", [UserRole.ARRACHI])
        self.assertEqual(user.password_plain, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

        user.set_password("Boshqa-Parol-2026")
        user.save()
        self.assertEqual(user.password_plain, "Boshqa-Parol-2026")
        self.assertTrue(user.check_password("Boshqa-Parol-2026"))

    def test_admin_sees_the_phone_and_password_on_the_worker_list(self):
        make_user("bobur", [UserRole.ARRACHI], phone="+998901234567", full_name="Bobur Karimov")
        self.client.force_login(make_admin())

        response = self.client.get(reverse("users:user_list"))
        self.assertContains(response, "+998 90 123 45 67")
        self.assertContains(response, PASSWORD)

    def test_a_worker_never_sees_the_worker_list(self):
        self.client.force_login(make_user("bobur", [UserRole.ARRACHI]))
        self.assertEqual(self.client.get(reverse("users:user_list")).status_code, 403)

    def test_admin_resetting_a_password_updates_both_copies(self):
        worker = make_user("bobur", [UserRole.ARRACHI])
        self.client.force_login(make_admin())

        self.client.post(
            reverse("users:user_edit", args=[worker.pk]),
            {
                "full_name": worker.full_name,
                "phone": worker.phone,
                "roles": [UserRole.ARRACHI],
                "is_approved": "on",
                "is_active": "on",
                "new_password": "Yangi-Parol-2026",
            },
        )
        worker.refresh_from_db()
        self.assertEqual(worker.password_plain, "Yangi-Parol-2026")
        self.assertTrue(worker.check_password("Yangi-Parol-2026"))


class AdminDoesNotTakeStockTests(TestCase):
    """Booking arrivals is the admin's job; carrying material away is the worker's."""

    def setUp(self):
        self.admin = make_admin()
        self.worker = make_user("bobur", [UserRole.ARRACHI])
        self.mdf = make_material("MDF 16 mm", allowed_roles=[UserRole.ARRACHI])
        stock_up(self.mdf, self.admin, 10, unit_price="38.50")

    def test_service_refuses_a_whole_withdrawal_by_an_admin(self):
        with self.assertRaises(PermissionDenied):
            services.withdraw_whole_material(material=self.mdf, user=self.admin, quantity=1)
        self.mdf.whole_stock.refresh_from_db()
        self.assertEqual(self.mdf.whole_stock.quantity, Decimal("10.00"))

    def test_service_refuses_an_offcut_withdrawal_by_an_admin(self):
        offcut = services.register_offcut(
            material=self.mdf, user=self.worker, length_mm=800, width_mm=450, quantity=2
        )
        with self.assertRaises(PermissionDenied):
            services.withdraw_offcut(offcut=offcut, user=self.admin, quantity=1)
        offcut.refresh_from_db()
        self.assertEqual(offcut.quantity, 2)

    def test_admin_still_books_arrivals(self):
        entry = services.record_kirim(
            material=self.mdf, user=self.admin, quantity=5, unit_price_usd=Decimal("40.00")
        )
        self.assertEqual(entry.stock_after, Decimal("15.00"))

    def test_withdrawal_pages_are_closed_to_the_admin(self):
        self.client.force_login(self.admin)
        stock = self.mdf.whole_stock
        self.assertEqual(
            self.client.get(reverse("inventory:withdraw_whole", args=[stock.pk])).status_code, 403
        )

    def test_withdrawal_api_is_closed_to_the_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/api/whole-materials/{self.mdf.whole_stock.pk}/withdraw/",
            {"quantity": "1"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_the_admin_sees_no_oldim_button_on_the_inventory_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("inventory:home"))
        self.assertContains(response, "MDF 16 mm")
        self.assertNotContains(response, "Oldim")
        self.assertContains(response, "Ishchi oladi")

    def test_the_worker_still_sees_and_uses_oldim(self):
        self.client.force_login(self.worker)
        response = self.client.get(reverse("inventory:home"))
        self.assertContains(response, "Oldim")

        entry = services.withdraw_whole_material(material=self.mdf, user=self.worker, quantity=2)
        self.assertEqual(entry.stock_after, Decimal("8.00"))


class WorkerSeesNoMoneyTests(TestCase):
    """A worker reads quantities and their own history; never prices or the dashboard."""

    def setUp(self):
        self.admin = make_admin()
        self.worker = make_user("bobur", [UserRole.ARRACHI])
        self.mdf = make_material("MDF 16 mm", allowed_roles=[UserRole.ARRACHI])
        stock_up(self.mdf, self.admin, 10, unit_price="38.50")
        services.withdraw_whole_material(material=self.mdf, user=self.worker, quantity=2)

    def test_dashboard_and_reports_are_admin_only(self):
        self.client.force_login(self.worker)
        for name in ("inventory:dashboard", "inventory:reports", "inventory:kirim"):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_no_dollar_figure_reaches_the_worker_pages(self):
        self.client.force_login(self.worker)
        for name in ("inventory:home", "inventory:my_history"):
            with self.subTest(view=name):
                body = self.client.get(reverse(name)).content.decode()
                self.assertNotIn("38.50", body)
                self.assertNotIn("$", body)

    def test_the_api_drops_price_keys_for_a_worker(self):
        self.client.force_login(self.worker)
        row = self.client.get("/api/whole-materials/").json()["results"][0]
        self.assertNotIn("unit_price_usd", row)
        self.assertNotIn("total_value_usd", row)
        self.assertEqual(row["quantity"], "8.00")

        entry = self.client.get("/api/transactions/").json()["results"][0]
        self.assertNotIn("total_price_usd", entry)
        self.assertEqual(entry["quantity_changed"], "-2.00")

    def test_the_worker_history_shows_only_their_own_movements(self):
        other = make_user("sardor", [UserRole.ARRACHI])
        services.withdraw_whole_material(material=self.mdf, user=other, quantity=1)

        self.client.force_login(self.worker)
        rows = self.client.get("/api/transactions/").json()
        self.assertEqual(rows["count"], 1)
        self.assertEqual(rows["results"][0]["user"], self.worker.display_name)
