"""
Load a believable demo workshop: an admin, five workers (one still pending),
thirteen materials and six months of Kirim / Chiqim / offcut history.

    python manage.py seed_demo                    # local only (DJANGO_DEBUG=True)
    python manage.py seed_demo --password "..."   # choose the demo password

Demo logins (local development only): admin, arrachi, roverchi, kraskachi,
sborkachi — all with the password below unless --password is given. The
pending account "yangi_ishchi" demonstrates the approval queue.
"""

import datetime as dt
import random
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.inventory import services
from apps.inventory.models import BaseMaterial, LeftoverOffcut, MaterialCategory, MeasurementUnit
from apps.users.models import User, UserRole

DEFAULT_DEMO_PASSWORD = "ombor-demo-2026"

WORKERS = [
    # username, full name, roles, approved  (phones are handed out below, +99890 000 00 0N)
    ("arrachi", "Bobur Karimov", [UserRole.ARRACHI], True),
    ("roverchi", "Sardor Aliyev", [UserRole.ROVERCHI, UserRole.ARRACHI], True),
    ("kraskachi", "Dilshod Rahimov", [UserRole.KRASKACHI], True),
    ("sborkachi", "Jasur Tursunov", [UserRole.SBORKACHI, UserRole.SHKURKACHI], True),
    ("yangi_ishchi", "Otabek Nazarov", [UserRole.SHKURKACHI], False),
]

# name, category, unit, container, minimum, (length, width), monthly kirim, unit price USD, monthly use share
MATERIALS = [
    ("MDF 16 mm oq", MaterialCategory.SHEET, MeasurementUnit.PIECE, None, 10, (2750, 1830), 30, "38.50", 0.85),
    ("LDSP 18 mm yong'oq", MaterialCategory.SHEET, MeasurementUnit.PIECE, None, 8, (2800, 2070), 20, "46.00", 0.80),
    ("Fanera 12 mm", MaterialCategory.SHEET, MeasurementUnit.PIECE, None, 6, (2440, 1220), 12, "27.40", 0.90),
    ("HDF 3 mm orqa devor", MaterialCategory.SHEET, MeasurementUnit.PIECE, None, 6, (2800, 2070), 15, "9.80", 0.75),
    ("Akril kraska oq", MaterialCategory.LIQUID, MeasurementUnit.LITER, "12", 24, None, 48, "4.35", 0.90),
    ("Poliuretan lak, yarim mat", MaterialCategory.LIQUID, MeasurementUnit.LITER, "5", 10, None, 25, "7.90", 0.80),
    ("Gruntovka", MaterialCategory.LIQUID, MeasurementUnit.KILOGRAM, "25", 25, None, 50, "2.10", 0.70),
    ("PVX lenta 22 mm oq", MaterialCategory.LINEAR, MeasurementUnit.METER, None, 200, None, 600, "0.12", 0.80),
    ("Shkurka P240 rulon", MaterialCategory.LINEAR, MeasurementUnit.METER, None, 20, None, 50, "0.95", 0.85),
    ("Petlya 35 mm, yumshoq yopilish", MaterialCategory.HARDWARE, MeasurementUnit.PIECE, None, 100, None, 200, "1.15", 0.95),
    ("Samarez 4×16", MaterialCategory.HARDWARE, MeasurementUnit.PIECE, None, 2000, None, 5000, "0.012", 0.70),
    ("Tutqich 128 mm, qora", MaterialCategory.HARDWARE, MeasurementUnit.PIECE, None, 40, None, 80, "2.60", 0.75),
    ("Mebel oyog'i 100 mm", MaterialCategory.HARDWARE, MeasurementUnit.PIECE, None, 32, None, 64, "1.80", 0.60),
]

#: These end the run below their minimum, so the purchase list has something to show.
END_LOW = {"Fanera 12 mm", "Akril kraska oq", "Petlya 35 mm, yumshoq yopilish"}

WORKER_FOR_CATEGORY = {
    MaterialCategory.SHEET: ["arrachi", "roverchi"],
    MaterialCategory.LIQUID: ["kraskachi"],
    MaterialCategory.LINEAR: ["sborkachi"],
    MaterialCategory.HARDWARE: ["sborkachi"],
}

ORDERS = ["Oshxona — Yunusobod", "Shkaf-kupe — Chilonzor", "Yotoqxona to'plami", "Ofis stollari", "Bolalar xonasi", ""]
OFFCUT_SPOTS = ["2-stellaj, yuqori polka", "1-stellaj, pastda", "Arra stanogi yonida", "Devor tagida", ""]


class Command(BaseCommand):
    help = "Load demo users, materials and six months of history (local development only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=DEFAULT_DEMO_PASSWORD, help="Password for every demo account.")
        parser.add_argument("--force", action="store_true", help="Allow running with DJANGO_DEBUG off.")

    def handle(self, *args, password, force, **options):
        if not settings.DEBUG and not force:
            raise CommandError("seed_demo is for local development. Use --force to run it with DEBUG off.")
        if BaseMaterial.objects.exists():
            raise CommandError("Materials already exist; demo data is only loaded into an empty database.")
        self.rng = random.Random(2026)
        with transaction.atomic():
            users = self._users(password)
            self._history(users)
        self.stdout.write(self.style.SUCCESS(
            f"Demo data loaded: {User.objects.count()} users, {BaseMaterial.objects.count()} materials, "
            f"{LeftoverOffcut.objects.count()} offcuts."
        ))
        self.stdout.write("Log in with a phone number and the demo password:")
        for user in User.objects.order_by("phone"):
            self.stdout.write(f"  {user.phone_display:<22} {user.full_name}  ({', '.join(user.role_labels) or 'pending'})")

    # ------------------------------------------------------------------
    def _users(self, password):
        admin_phone = "+998900000001"
        admin = User.objects.create_superuser(
            admin_phone, password=password, full_name="Akmal Rustamov", phone=admin_phone
        )
        users = {"admin": admin}
        for index, (username, full_name, roles, approved) in enumerate(WORKERS, start=2):
            phone = f"+99890000000{index}"
            user = User(
                username=phone,
                phone=phone,
                full_name=full_name,
                roles=[str(r) for r in roles],
                is_approved=approved,
            )
            user.set_password(password)
            user.save()
            if approved:
                user.approved_by, user.approved_at = admin, timezone.now()
                user.save(update_fields=["approved_by", "approved_at"])
            users[username] = user
        return users

    def _at(self, day: dt.date, hour: int):
        tz = timezone.get_current_timezone()
        minute = self.rng.randint(0, 59)
        return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)

    def _history(self, users):
        today = timezone.localdate()
        now = timezone.localtime()
        months = []
        year, month = today.year, today.month
        for _ in range(6):
            months.append((year, month))
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        months.reverse()

        admin = users["admin"]
        materials = []
        for name, category, unit, container, minimum, dims, monthly, price, use in MATERIALS:
            with mock.patch("django.utils.timezone.now", return_value=self._at(dt.date(*months[0], 1), 8)):
                material = services.create_material(
                    name=name,
                    category=category,
                    unit_of_measure=unit,
                    container_size=Decimal(container) if container else None,
                    minimum_threshold=Decimal(minimum),
                    length_mm=dims[0] if dims else None,
                    width_mm=dims[1] if dims else None,
                )
            materials.append((material, monthly, Decimal(price), use))

        for index, (year, month) in enumerate(months):
            is_current = index == len(months) - 1
            last_day = today.day if is_current else 28
            for material, monthly, price, use in materials:
                # Kirim on the 2nd-4th, price drifting a few percent month to month
                drift = Decimal(str(1 + self.rng.uniform(-0.04, 0.06))).quantize(Decimal("0.001"))
                unit_price = (price * drift).quantize(Decimal("0.0001"))
                quantity = Decimal(monthly)
                if material.unit_of_measure == MeasurementUnit.LITER and material.container_size:
                    quantity = material.container_size * max(1, round(monthly / float(material.container_size)))
                arrival = min(self._at(dt.date(year, month, self.rng.randint(2, 4)), 9), now - dt.timedelta(hours=1))
                with mock.patch("django.utils.timezone.now", return_value=arrival):
                    services.record_kirim(
                        material=material, user=admin, quantity=quantity, total_price_usd=unit_price * quantity,
                        notes=self.rng.choice(["Faktura №" + str(self.rng.randint(1000, 9999)), "Toshkent, Sergeli bozori", ""]),
                    )

                # Chiqim through the month by the trade that uses it
                share = use * (0.55 if is_current else 1.0)
                to_use = Decimal(monthly) * Decimal(str(share))
                workers = WORKER_FOR_CATEGORY[material.category]
                pulls = self.rng.randint(4, 8)
                for _ in range(pulls):
                    step = to_use / pulls
                    if material.requires_integer_quantity:
                        step = Decimal(max(1, round(float(step))))
                    else:
                        step = step.quantize(Decimal("0.5")) or Decimal("0.5")
                    day = dt.date(year, month, self.rng.randint(5, max(5, last_day)))
                    when = self._at(day, self.rng.randint(8, 17))
                    if when > now:
                        when = now - dt.timedelta(minutes=self.rng.randint(5, 600))
                    material.refresh_from_db()
                    if step > material.whole_stock.quantity:
                        break
                    with mock.patch("django.utils.timezone.now", return_value=when):
                        services.withdraw_whole_material(
                            material=material, user=users[self.rng.choice(workers)], quantity=step,
                            notes=self.rng.choice(ORDERS),
                        )

        # Leave a few materials below their minimum so the purchase list has work in it.
        for material, *_ in materials:
            if material.name not in END_LOW:
                continue
            material.refresh_from_db()
            target = (material.minimum_threshold * Decimal("0.5")).quantize(Decimal("1"))
            excess = material.whole_stock.quantity - target
            if excess > 0:
                with mock.patch("django.utils.timezone.now", return_value=now - dt.timedelta(hours=3)):
                    services.withdraw_whole_material(
                        material=material, user=users[WORKER_FOR_CATEGORY[material.category][0]],
                        quantity=excess, notes="Katta buyurtma — Oshxona, Mirzo Ulug'bek",
                    )

        # Offcuts left after cutting sheets, logged by the sawyers
        sheets = [m for m, *_ in materials if m.category == MaterialCategory.SHEET]
        pieces = [(1200, 600), (800, 450), (1830, 300), (640, 520), (2100, 380), (950, 700), (600, 400), (1500, 250)]
        for i, (length, width) in enumerate(pieces):
            material = sheets[i % len(sheets)]
            stock = material.whole_stock
            length = min(length, max(stock.length_mm, stock.width_mm))
            width = min(width, min(stock.length_mm, stock.width_mm))
            when = now - dt.timedelta(days=self.rng.randint(0, 20), hours=self.rng.randint(0, 8))
            with mock.patch("django.utils.timezone.now", return_value=when):
                offcut = services.register_offcut(
                    material=material, user=users[self.rng.choice(["arrachi", "roverchi"])],
                    length_mm=length, width_mm=width, quantity=self.rng.choice([1, 1, 2, 3]),
                    notes=self.rng.choice(OFFCUT_SPOTS),
                )
            if i in (2, 5):
                with mock.patch("django.utils.timezone.now", return_value=when + dt.timedelta(days=1)):
                    services.withdraw_offcut(offcut=offcut, user=users["roverchi"], quantity=1, notes="Polka uchun")
