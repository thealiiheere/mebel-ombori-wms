from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import User, normalize_phone, normalize_roles


@transaction.atomic
def register_worker(*, phone: str, password: str, full_name: str, roles: list[str]) -> User:
    """Create a self-registered worker. Access stays locked until an admin approves.

    The phone number is the login identifier and is mirrored into ``username``, which
    is what Django's auth machinery still authenticates against.
    """
    roles = normalize_roles(roles)
    if not roles:
        raise ValidationError({"roles": _("Kamida bitta kasbni tanlang.")})
    phone = normalize_phone(phone)
    if User.objects.filter(phone=phone).exists():
        raise ValidationError({"phone": _("Bu raqam allaqachon ro'yxatdan o'tgan.")})
    user = User(
        username=phone,
        phone=phone,
        full_name=full_name,
        roles=roles,
        is_approved=False,
        is_active=True,
    )
    user.set_password(password)
    user.full_clean()
    user.save()
    return user


@transaction.atomic
def approve_user(user: User, *, approved_by: User, roles: list[str] | None = None) -> User:
    if roles is not None:
        user.roles = normalize_roles(roles)
    if not user.roles:
        raise ValidationError({"roles": _("Tasdiqlashdan oldin kamida bitta rol belgilang.")})
    user.is_approved = True
    user.is_active = True
    user.approved_at = timezone.now()
    user.approved_by = approved_by
    user.save()
    return user


@transaction.atomic
def reject_registration(user: User) -> None:
    """Discard a pending registration request (pending accounts have no ledger history)."""
    if user.is_approved or user.is_superuser:
        raise ValidationError(_("Faqat tasdiqlanmagan so'rovlarni rad etish mumkin."))
    user.delete()
