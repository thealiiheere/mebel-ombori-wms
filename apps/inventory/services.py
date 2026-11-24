"""
Write-side business operations. Every stock movement goes through here so that
locking, validation and ledger entries are applied consistently for the HTML
views, the REST API and management commands alike.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import (
    BaseMaterial,
    InventoryTransaction,
    LeftoverOffcut,
    TransactionType,
    WholeMaterial,
    default_roles_for_category,
)
from .selectors import user_can_access_material

logger = logging.getLogger("wms.security")

CENTS = Decimal("0.01")
PRICE_PLACES = Decimal("0.0001")
ZERO = Decimal("0")


class InsufficientStockError(ValidationError):
    """Requested quantity exceeds what is on the shelf."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _decimal(value, field: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({field: _("Noto'g'ri raqam.")}) from exc
    if not number.is_finite():
        raise ValidationError({field: _("Noto'g'ri raqam.")})
    return number


def _positive_quantity(material: BaseMaterial, value, field: str = "quantity") -> Decimal:
    quantity = _decimal(value, field)
    if quantity <= 0:
        raise ValidationError({field: _("Miqdor 0 dan katta bo'lishi kerak.")})
    if quantity != quantity.quantize(CENTS):
        raise ValidationError({field: _("Miqdor ko'pi bilan 2 xonali kasr bo'lishi mumkin.")})
    if material.requires_integer_quantity and quantity != quantity.to_integral_value():
        raise ValidationError(
            {field: _("%(name)s faqat butun sonda hisoblanadi (%(unit)s).") % {
                "name": material.name, "unit": material.unit_short}}
        )
    return quantity.quantize(CENTS)


def _require_admin(user) -> None:
    if not (getattr(user, "can_use_system", False) and user.is_warehouse_admin):
        logger.warning(
            "Security: unauthorized admin operation attempted by user '%s' (id: %s)",
            getattr(user, "username", "anonymous"),
            getattr(user, "pk", None),
        )
        raise PermissionDenied(_("Bu amal faqat administratorlar uchun."))


def _require_access(user, material: BaseMaterial) -> None:
    if not user_can_access_material(user, material):
        logger.warning(
            "Security: material access denied for user '%s' (roles: %s) to material '%s' (id: %s, category: %s)",
            getattr(user, "username", "anonymous"),
            getattr(user, "worker_roles", []),
            material.name,
            material.pk,
            material.category,
        )
        raise PermissionDenied(_("Bu material sizning kasbingiz uchun ruxsat etilmagan."))


def _require_taker(user) -> None:
    """Taking stock off the shelf is a worker's action, never an administrator's.

    The administrator books arrivals, sets minimums and reads the history; the
    ledger should name the person who actually carried the material away. Checked
    here rather than only in the templates, so the REST API is bound by it too.
    """
    if getattr(user, "is_warehouse_admin", False):
        logger.warning(
            "Security: warehouse administrator '%s' (id: %s) attempted stock withdrawal",
            getattr(user, "username", "anonymous"),
            getattr(user, "pk", None),
        )
        raise PermissionDenied(_("Administrator ombordan material olmaydi — buni ishchi bajaradi."))


def _locked_stock(material: BaseMaterial) -> WholeMaterial:
    """Fetch (creating if missing) and row-lock the WholeMaterial for ``material``."""
    WholeMaterial.objects.get_or_create(material=material)
    return WholeMaterial.objects.select_for_update().get(material_id=material.pk)


# ---------------------------------------------------------------------------
# Material catalogue
# ---------------------------------------------------------------------------
@transaction.atomic
def create_material(
    *,
    name: str,
    category: str,
    unit_of_measure: str,
    allowed_roles: list[str] | None = None,
    container_size: Decimal | None = None,
    minimum_threshold: Decimal = Decimal("0.00"),
    length_mm: int | None = None,
    width_mm: int | None = None,
) -> BaseMaterial:
    material = BaseMaterial(
        name=name.strip(),
        category=category,
        unit_of_measure=unit_of_measure,
        allowed_roles=list(allowed_roles) if allowed_roles else default_roles_for_category(category),
        container_size=container_size,
        minimum_threshold=minimum_threshold or Decimal("0.00"),
    )
    material.full_clean()
    material.save()
    stock = WholeMaterial(material=material, length_mm=length_mm, width_mm=width_mm)
    stock.full_clean()
    stock.save()
    return material


# ---------------------------------------------------------------------------
# Kirim: stock arrival (admin only)
# ---------------------------------------------------------------------------
def record_kirim(
    *,
    material: BaseMaterial,
    user,
    quantity,
    unit_price_usd=None,
    total_price_usd=None,
    notes: str = "",
) -> InventoryTransaction:
    """
    Book a purchase. Pass *either* the per-unit price or the total batch price (USD).

    The material's ``unit_price_usd`` becomes the weighted average cost of the
    stock on hand plus this batch, so the valuation stays accurate when purchase
    prices change between batches.
    """
    _require_admin(user)
    quantity = _positive_quantity(material, quantity)

    if (unit_price_usd is None) == (total_price_usd is None):
        raise ValidationError(_("Birlik narxi yoki umumiy partiya narxidan bittasini kiriting."))
    if total_price_usd is not None:
        batch_total = _decimal(total_price_usd, "total_price_usd")
        if batch_total < 0:
            raise ValidationError({"total_price_usd": _("Narx manfiy bo'lishi mumkin emas.")})
        unit_price = (batch_total / quantity).quantize(PRICE_PLACES, rounding=ROUND_HALF_UP)
    else:
        unit_price = _decimal(unit_price_usd, "unit_price_usd")
        if unit_price < 0:
            raise ValidationError({"unit_price_usd": _("Narx manfiy bo'lishi mumkin emas.")})
        unit_price = unit_price.quantize(PRICE_PLACES, rounding=ROUND_HALF_UP)
        batch_total = unit_price * quantity
    batch_total = batch_total.quantize(CENTS, rounding=ROUND_HALF_UP)

    with transaction.atomic():
        stock = _locked_stock(material)
        base = BaseMaterial.objects.select_for_update().get(pk=material.pk)
        if not base.is_active:
            raise ValidationError(_("Arxivlangan materialga kirim qilib bo'lmaydi. Avval uni faollashtiring."))

        old_quantity = stock.quantity
        new_quantity = old_quantity + quantity
        if old_quantity <= 0:
            average_price = unit_price
        else:
            old_value = old_quantity * base.unit_price_usd
            average_price = ((old_value + batch_total) / new_quantity).quantize(PRICE_PLACES, rounding=ROUND_HALF_UP)

        base.unit_price_usd = average_price
        base.save(update_fields=["unit_price_usd", "updated_at"])
        stock.quantity = new_quantity
        stock.save(update_fields=["quantity", "updated_at"])

        entry = InventoryTransaction.objects.create(
            transaction_type=TransactionType.KIRIM,
            material=base,
            user=user,
            quantity_changed=quantity,
            unit_price_usd=unit_price,
            total_price_usd=batch_total,
            stock_after=new_quantity,
            notes=notes.strip(),
        )
    material.unit_price_usd = average_price
    return entry


# ---------------------------------------------------------------------------
# Chiqim: worker withdrawals
# ---------------------------------------------------------------------------
def withdraw_whole_material(*, material: BaseMaterial, user, quantity, notes: str = "") -> InventoryTransaction:
    """Worker takes whole units (sheets, litres, pieces...) from the shelf."""
    _require_taker(user)
    _require_access(user, material)
    quantity = _positive_quantity(material, quantity)

    with transaction.atomic():
        stock = _locked_stock(material)
        base = BaseMaterial.objects.get(pk=material.pk)
        if not base.is_active:
            raise ValidationError(_("Bu material arxivlangan."))
        if quantity > stock.quantity:
            raise InsufficientStockError(
                {"quantity": _("Omborda faqat %(amount)s %(unit)s bor.") % {
                    "amount": f"{stock.quantity.normalize():f}", "unit": base.unit_short}},
                code="insufficient_stock",
            )
        stock.quantity -= quantity
        stock.save(update_fields=["quantity", "updated_at"])

        unit_price = base.unit_price_usd
        return InventoryTransaction.objects.create(
            transaction_type=TransactionType.CHIQIM,
            material=base,
            user=user,
            quantity_changed=-quantity,
            unit_price_usd=unit_price,
            total_price_usd=(unit_price * quantity).quantize(CENTS, rounding=ROUND_HALF_UP),
            stock_after=stock.quantity,
            notes=notes.strip(),
        )


def withdraw_offcut(*, offcut: LeftoverOffcut, user, quantity: int = 1, notes: str = "") -> InventoryTransaction:
    """Worker reuses offcut piece(s). The record is removed once no pieces remain."""
    _require_taker(user)
    _require_access(user, offcut.material)
    try:
        pieces = int(quantity)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"quantity": _("Butun son kiriting.")}) from exc
    if pieces < 1 or Decimal(str(quantity)) != pieces:
        raise ValidationError({"quantity": _("Kamida 1 dona (butun son) kiriting.")})

    with transaction.atomic():
        locked = LeftoverOffcut.objects.select_for_update().filter(pk=offcut.pk).first()
        if locked is None:
            raise InsufficientStockError(_("Bu qoldiq allaqachon olingan."), code="offcut_gone")
        if pieces > locked.quantity:
            raise InsufficientStockError(
                {"quantity": _("Bu qoldiqdan faqat %(count)s dona bor.") % {"count": locked.quantity}},
                code="insufficient_stock",
            )
        length_mm, width_mm, material_id = locked.length_mm, locked.width_mm, locked.material_id
        remaining = locked.quantity - pieces
        if remaining == 0:
            locked.delete()
        else:
            locked.quantity = remaining
            locked.save(update_fields=["quantity"])

        return InventoryTransaction.objects.create(
            transaction_type=TransactionType.CHIQIM,
            material_id=material_id,
            user=user,
            quantity_changed=Decimal(-pieces),
            unit_price_usd=ZERO,
            total_price_usd=ZERO,
            is_offcut=True,
            offcut_length_mm=length_mm,
            offcut_width_mm=width_mm,
            notes=notes.strip(),
        )


# ---------------------------------------------------------------------------
# Qoldiq: offcut registration
# ---------------------------------------------------------------------------
def register_offcut(
    *,
    material: BaseMaterial,
    user,
    length_mm: int,
    width_mm: int,
    quantity: int = 1,
    notes: str = "",
) -> LeftoverOffcut:
    """Log a reusable leftover piece after cutting a sheet."""
    _require_access(user, material)
    if not material.supports_offcuts:
        raise ValidationError({"material": _("Bu turdagi material uchun qoldiq qayd etilmaydi.")})

    errors = {}
    for field, value in (("length_mm", length_mm), ("width_mm", width_mm), ("quantity", quantity)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors[field] = _("Musbat butun son kiriting.")
    if errors:
        raise ValidationError(errors)

    stock = WholeMaterial.objects.filter(material=material).first()
    if stock and stock.length_mm and stock.width_mm:
        piece = sorted((length_mm, width_mm))
        sheet = sorted((stock.length_mm, stock.width_mm))
        if piece[0] > sheet[0] or piece[1] > sheet[1]:
            raise ValidationError(
                _("Qoldiq o'lchami to'liq listdan (%(length)sx%(width)s mm) katta bo'lishi mumkin emas.")
                % {"length": stock.length_mm, "width": stock.width_mm}
            )

    with transaction.atomic():
        offcut = LeftoverOffcut.objects.create(
            material=material,
            length_mm=length_mm,
            width_mm=width_mm,
            quantity=quantity,
            notes=notes.strip(),
            created_by=user,
        )
        InventoryTransaction.objects.create(
            transaction_type=TransactionType.OFFCUT_DEPOSIT,
            material=material,
            user=user,
            quantity_changed=Decimal(quantity),
            unit_price_usd=ZERO,
            total_price_usd=ZERO,
            is_offcut=True,
            offcut_length_mm=length_mm,
            offcut_width_mm=width_mm,
            notes=notes.strip(),
        )
    return offcut
