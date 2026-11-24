import re

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext, pgettext_lazy


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", _("Administrator")
    ARRACHI = "ARRACHI", pgettext_lazy("trade", "Arrachi")
    KRASKACHI = "KRASKACHI", pgettext_lazy("trade", "Kraskachi")
    SHKURKACHI = "SHKURKACHI", pgettext_lazy("trade", "Shkurkachi")
    ROVERCHI = "ROVERCHI", pgettext_lazy("trade", "Roverchi")
    SBORKACHI = "SBORKACHI", pgettext_lazy("trade", "Sborkachi")


class WorkerRole(models.Model):
    """A trade (kasb) workers register with. ADMIN is not a trade; it stays in UserRole.

    The five original trades are seeded by migration with the same codes as UserRole,
    so existing ``User.roles`` and ``BaseMaterial.allowed_roles`` values keep working.
    """

    code = models.CharField(_("Kod"), max_length=40, unique=True)
    name = models.CharField(_("Nomi"), max_length=100)
    sort_order = models.PositiveIntegerField(_("Tartib"), default=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = _("kasb")
        verbose_name_plural = _("kasblar")

    def __str__(self) -> str:
        return self.name


# Trade codes and names are read on almost every page, cached thread-safely via django.core.cache.
ROLE_CACHE_KEY = "wms:worker_role_choices"


def clear_role_cache(*args, **kwargs) -> None:
    cache.delete(ROLE_CACHE_KEY)


def localized_trade_name(name: str) -> str:
    """The five built-in trade names are translated; trades admins added are shown as typed."""
    return pgettext("trade", name) if name else name


def worker_role_choices() -> list[tuple[str, str]]:
    """Trades as (code, name) pairs in display order and the current language. Usable as form ``choices``."""
    raw = cache.get(ROLE_CACHE_KEY)
    if raw is None:
        raw = list(WorkerRole.objects.values_list("code", "name"))
        cache.set(ROLE_CACHE_KEY, raw, timeout=300)
    return [(code, localized_trade_name(name)) for code, name in raw]


def worker_role_codes() -> list[str]:
    return [code for code, _ in worker_role_choices()]


def all_role_choices() -> list[tuple[str, str]]:
    return [(UserRole.ADMIN.value, str(UserRole.ADMIN.label)), *worker_role_choices()]


def role_label(code: str) -> str:
    if code == UserRole.ADMIN:
        return str(UserRole.ADMIN.label)
    return dict(worker_role_choices()).get(code, code)


def normalize_roles(roles) -> list[str]:
    """De-duplicate roles; ADMIN first, then trades in display order. Unknown codes are dropped."""
    wanted = set(roles or [])
    return [role for role in [UserRole.ADMIN.value, *worker_role_codes()] if role in wanted]


def validate_role_list(value) -> None:
    if not isinstance(value, list):
        raise ValidationError(_("Rollar ro'yxat (list) ko'rinishida bo'lishi kerak."))
    known = {UserRole.ADMIN.value, *worker_role_codes()}
    unknown = [role for role in value if role not in known]
    if unknown:
        raise ValidationError(_("Noma'lum rol(lar): %(roles)s"), params={"roles": ", ".join(map(str, unknown))})


# ---------------------------------------------------------------------------
# Phone numbers (the login identifier)
# ---------------------------------------------------------------------------
#: Uzbek mobile numbers: +998 followed by nine digits.
PHONE_PREFIX = "+998"
PHONE_DIGITS = 9


def normalize_phone(raw) -> str:
    """Accept what a worker actually types and return one canonical form.

    ``90 123 45 67``, ``+998 90 123 45 67``, ``998901234567`` and ``0901234567``
    all become ``+998901234567``, so the same person cannot register twice with
    the same number written two ways.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == PHONE_DIGITS + 3 and digits.startswith("998"):
        digits = digits[3:]
    elif len(digits) == PHONE_DIGITS + 1 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != PHONE_DIGITS:
        raise ValidationError(
            _("Telefon raqamini +998 90 123 45 67 ko'rinishida kiriting."), code="invalid_phone"
        )
    return PHONE_PREFIX + digits


def phone_display(phone: str) -> str:
    """``+998901234567`` -> ``+998 90 123 45 67`` for reading off a screen."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) != PHONE_DIGITS + 3:
        return phone or ""
    d = digits[3:]
    return f"+{digits[:3]} {d[:2]} {d[2:5]} {d[5:7]} {d[7:]}"


def validate_phone(value) -> None:
    normalize_phone(value)


class UserQuerySet(models.QuerySet):
    def pending_approval(self):
        return self.filter(is_approved=False, is_superuser=False, is_active=True)

    def approved(self):
        return self.filter(models.Q(is_approved=True) | models.Q(is_superuser=True))


class UserManager(DjangoUserManager.from_queryset(UserQuerySet)):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_approved", True)
        extra_fields.setdefault("roles", [UserRole.ADMIN.value])
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    full_name = models.CharField(_("To'liq ism"), max_length=255, blank=True)
    phone = models.CharField(
        _("Telefon raqami"),
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_phone],
        help_text=_("Tizimga shu raqam bilan kiriladi."),
    )
    #: The password as typed, kept readable so an administrator can tell a worker
    #: their own password over the phone. Requested deliberately; it means a copy of
    #: this database exposes every worker account, so keep backups and admin access
    #: as tightly held as the warehouse itself.
    password_plain = models.CharField(_("Parol"), max_length=128, blank=True, default="")
    roles = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_role_list],
        help_text="List of assigned UserRole strings",
    )
    is_approved = models.BooleanField(
        default=False, help_text="Admin approval status for worker login"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = UserManager()

    class Meta:
        ordering = ["full_name", "username"]
        verbose_name = _("foydalanuvchi")
        verbose_name_plural = _("foydalanuvchilar")

    def __str__(self) -> str:
        return self.full_name or self.username

    def save(self, *args, **kwargs):
        self.roles = normalize_roles(self.roles)
        super().save(*args, **kwargs)

    def set_password(self, raw_password):
        """Hash the password *and* keep the readable copy the admin screens show.

        Every path that sets a password (registration, the admin's reset field,
        ``createsuperuser``, ``seed_demo``) goes through here, so the two stay in step.
        """
        super().set_password(raw_password)
        self.password_plain = raw_password or ""

    # --- Phone ------------------------------------------------------------
    @property
    def phone_display(self) -> str:
        return phone_display(self.phone)

    @property
    def login_id(self) -> str:
        """What the person types to log in: their phone, or the username for old accounts."""
        return self.phone_display or self.username

    # --- Role helpers -----------------------------------------------------
    def has_role(self, role_name: str) -> bool:
        return self.is_superuser or role_name in self.roles or UserRole.ADMIN in self.roles

    @property
    def is_warehouse_admin(self) -> bool:
        return self.is_superuser or UserRole.ADMIN in (self.roles or [])

    @property
    def worker_roles(self) -> list[str]:
        trades = set(worker_role_codes())
        return [role for role in (self.roles or []) if role in trades]

    @property
    def can_use_system(self) -> bool:
        """Active and approved (superusers never need approval)."""
        return self.is_active and (self.is_approved or self.is_superuser)

    @property
    def role_labels(self) -> list[str]:
        return [role_label(role) for role in (self.roles or [])]

    @property
    def display_name(self) -> str:
        return self.full_name or self.username
