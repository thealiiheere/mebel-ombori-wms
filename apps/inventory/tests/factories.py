"""Small helpers for building test data through the real service layer."""

import datetime as dt
import itertools
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from apps.inventory import services
from apps.inventory.models import BaseMaterial, MaterialCategory, MeasurementUnit
from apps.users.models import User, UserRole

PASSWORD = "Ombor-test-Parol-91"


#: Hands out +998 90 000 00 NN so every test user has a distinct login phone.
_phone_counter = itertools.count(1)


def next_phone() -> str:
    return f"+99890000{next(_phone_counter):04d}"


def make_user(username: str, roles=(), *, approved: bool = True, admin: bool = False, **extra) -> User:
    roles = list(roles)
    if admin and UserRole.ADMIN not in roles:
        roles.append(UserRole.ADMIN)
    extra.setdefault("phone", next_phone())
    user = User(username=username, full_name=extra.pop("full_name", username.title()), roles=roles, is_approved=approved, **extra)
    user.set_password(PASSWORD)
    user.save()
    return user


def make_admin(username: str = "boss") -> User:
    return make_user(username, admin=True)


def make_material(
    name: str = "MDF 16 mm",
    category: str = MaterialCategory.SHEET,
    unit: str = MeasurementUnit.PIECE,
    *,
    allowed_roles=None,
    minimum=Decimal("0"),
    container_size=None,
    length_mm=None,
    width_mm=None,
) -> BaseMaterial:
    if category == MaterialCategory.SHEET and length_mm is None:
        length_mm, width_mm = 2750, 1830
    return services.create_material(
        name=name,
        category=category,
        unit_of_measure=unit,
        allowed_roles=allowed_roles,
        minimum_threshold=Decimal(minimum),
        container_size=container_size,
        length_mm=length_mm,
        width_mm=width_mm,
    )


def stock_up(material: BaseMaterial, admin: User, quantity, *, unit_price=None, total=None):
    if unit_price is None and total is None:
        unit_price = Decimal("10.00")
    return services.record_kirim(
        material=material, user=admin, quantity=Decimal(str(quantity)),
        unit_price_usd=None if unit_price is None else Decimal(str(unit_price)),
        total_price_usd=None if total is None else Decimal(str(total)),
    )


@contextmanager
def frozen_at(year: int, month: int, day: int, hour: int = 12):
    """Create rows as if it were the given local date (auto_now_add honours this)."""
    when = dt.datetime(year, month, day, hour, 0, tzinfo=timezone.get_current_timezone())
    with mock.patch("django.utils.timezone.now", return_value=when):
        yield when
