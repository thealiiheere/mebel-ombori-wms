import re
import unicodedata

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import (
    User,
    UserRole,
    WorkerRole,
    all_role_choices,
    localized_trade_name,
    normalize_phone,
    normalize_roles,
    worker_role_choices,
)


#: Lets ``partials/password_field.html`` flip the input between dots and plain text.
PASSWORD_EYE_ATTRS = {"x-bind:type": "show ? 'text' : 'password'"}


def with_eye(field):
    """Mark a password field so the shared partial can reveal it."""
    field.widget.attrs.update(PASSWORD_EYE_ATTRS)
    return field


class PhoneField(forms.CharField):
    """A phone number typed any way the worker likes, stored in one canonical form."""

    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Telefon raqami"))
        kwargs.setdefault("max_length", 25)
        super().__init__(**kwargs)
        self.widget.attrs.update(
            {
                "inputmode": "tel",
                "autocomplete": "tel",
                "placeholder": "+998 90 123 45 67",
            }
        )

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        return normalize_phone(value)


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": _("Login yoki parol noto'g'ri."),
        "inactive": _("Bu hisob o'chirilgan. Administratorga murojaat qiling."),
        "pending": _(
            "Hisobingiz hali tasdiqlanmagan. Administrator so'rovingizni ko'rib chiqqach, "
            "tizimga kira olasiz."
        ),
    }

    error_messages_extra = {"invalid_phone": _("Telefon raqami yoki parol noto'g'ri.")}

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        field = self.fields["username"]
        field.label = _("Telefon raqami")
        field.max_length = 25
        field.widget.attrs.update(
            {
                "autocapitalize": "none",
                "autocomplete": "tel",
                "inputmode": "tel",
                "autofocus": True,
                "placeholder": "+998 90 123 45 67",
            }
        )
        with_eye(self.fields["password"]).label = _("Parol")

    def clean_username(self):
        """Turn whatever was typed into the stored phone form.

        Anything that is not a phone number is passed through untouched, so an
        account made with ``createsuperuser`` can still sign in with its username.
        """
        value = self.cleaned_data.get("username", "")
        try:
            return normalize_phone(value)
        except forms.ValidationError:
            return value

    def clean(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            # The auth backend rejects unapproved users outright; detect that case
            # (correct password, not yet approved) so we can show a helpful message.
            user = (
                User._default_manager.filter(phone=username).first()
                or User._default_manager.filter(username=username).first()
            )
            if (
                user is not None
                and user.is_active
                and not user.is_superuser
                and not user.is_approved
                and user.check_password(password)
            ):
                raise forms.ValidationError(self.error_messages["pending"], code="pending")
        return super().clean()


class WorkerRegistrationForm(UserCreationForm):
    full_name = forms.CharField(label=_("To'liq ism (F.I.Sh.)"), max_length=255)
    phone = PhoneField(help_text=_("Tizimga shu raqam bilan kirasiz."))
    roles = forms.MultipleChoiceField(
        label=_("Kasbingiz"),
        choices=worker_role_choices,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Bir nechta kasbni tanlashingiz mumkin."),
        error_messages={"required": _("Kamida bitta kasbni tanlang.")},
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("full_name",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The phone number is the login identifier; ``username`` is derived from it.
        self.fields.pop("username", None)
        with_eye(self.fields["password1"]).label = _("Parol")
        self.fields["password1"].help_text = _("Kamida 8 ta belgi; faqat raqamlardan iborat bo'lmasin.")
        with_eye(self.fields["password2"]).label = _("Parolni takrorlang")
        self.fields["password2"].help_text = ""

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        if User.objects.filter(phone=phone).exists():
            raise forms.ValidationError(_("Bu raqam allaqachon ro'yxatdan o'tgan."))
        return phone

    def save(self, commit=True):
        user = super().save(commit=False)
        user.full_name = self.cleaned_data["full_name"].strip()
        user.phone = self.cleaned_data["phone"]
        user.username = user.phone
        user.roles = normalize_roles(self.cleaned_data["roles"])
        user.is_approved = False
        user.is_active = True
        if commit:
            user.save()
        return user


class RoleAssignmentForm(forms.ModelForm):
    """Used by admins when reviewing a pending registration."""

    roles = forms.MultipleChoiceField(
        label=_("Rollar"),
        choices=all_role_choices,
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": _("Kamida bitta rol belgilang.")},
    )

    class Meta:
        model = User
        fields = ("full_name", "roles")


class UserAdminEditForm(forms.ModelForm):
    """Used by admins to manage an existing account."""

    phone = PhoneField(required=False, help_text=_("Ishchi shu raqam bilan tizimga kiradi."))
    roles = forms.MultipleChoiceField(
        label=_("Rollar"),
        choices=all_role_choices,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    new_password = forms.CharField(
        label=_("Yangi parol"),
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", **PASSWORD_EYE_ATTRS}),
        help_text=_("Ishchi parolini unutgan bo'lsa, shu yerda yangisini o'rnating."),
    )

    class Meta:
        model = User
        fields = ("full_name", "phone", "roles", "is_approved", "is_active")
        labels = {"is_approved": _("Tasdiqlangan"), "is_active": _("Faol hisob")}
        # The model/Django defaults are English ("Admin approval status for worker login", "Designates whether...").
        help_texts = {
            "is_approved": _("Tasdiqlangan ishchi tizimga kira oladi."),
            "is_active": _("O'chirilsa, foydalanuvchi tizimga kira olmaydi; tarixi saqlanadi."),
        }

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)

    def clean_new_password(self):
        password = self.cleaned_data.get("new_password")
        if password:
            password_validation.validate_password(password, self.instance)
        return password

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        if phone and User.objects.filter(phone=phone).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("Bu raqam boshqa hisobga biriktirilgan."))
        return phone

    def clean(self):
        cleaned = super().clean()
        if self.acting_user is not None and self.instance.pk == self.acting_user.pk:
            if UserRole.ADMIN not in (cleaned.get("roles") or []) and not self.instance.is_superuser:
                self.add_error("roles", _("O'zingizdan administrator rolini olib tashlay olmaysiz."))
            if not cleaned.get("is_active", True) or not cleaned.get("is_approved", True):
                self.add_error(None, _("O'z hisobingizni o'chira yoki tasdiqdan chiqara olmaysiz."))
        if cleaned.get("is_approved") and not cleaned.get("roles"):
            self.add_error("roles", _("Tasdiqlangan foydalanuvchida kamida bitta rol bo'lishi kerak."))
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.roles = normalize_roles(self.cleaned_data.get("roles"))
        # The phone is the login identifier, so ``username`` follows it.
        if user.phone:
            user.username = user.phone
        if self.cleaned_data.get("new_password"):
            user.set_password(self.cleaned_data["new_password"])
        if commit:
            user.save()
        return user


class WorkerRoleForm(forms.ModelForm):
    """Admin: add a new trade (kasb). The code is generated from the name and never changes."""

    class Meta:
        model = WorkerRole
        fields = ("name",)
        labels = {"name": _("Kasb nomi")}
        help_texts = {"name": _("Masalan: Pardozchi. Ishchilar ro'yxatdan o'tishda shu nomni ko'radi.")}

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        # Compare with the stored names and with their translations, so "Маляр" can't duplicate Kraskachi.
        existing = WorkerRole.objects.values_list("name", flat=True)
        taken = {n.casefold() for n in existing} | {localized_trade_name(n).casefold() for n in existing}
        if name.casefold() in taken:
            raise forms.ValidationError(_("Bunday kasb allaqachon bor."))
        return name

    def save(self, commit=True):
        role = super().save(commit=False)
        role.code = trade_code_from(role.name)
        last = WorkerRole.objects.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0
        role.sort_order = last + 10
        if commit:
            role.save()
        return role


def unique_code(name: str, is_taken, fallback: str, max_length: int = 34) -> str:
    """'Pardozchi usta' -> 'PARDOZCHI_USTA', with _2, _3 ... appended while ``is_taken(code)``."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").upper()[:max_length] or fallback
    if not base[0].isalpha():
        base = f"{fallback}_{base}"[:max_length]
    code, n = base, 2
    while is_taken(code):
        code, n = f"{base}_{n}", n + 1
    return code


def trade_code_from(name: str) -> str:
    """A unique trade code for ``name``; never 'ADMIN'."""
    return unique_code(
        name, lambda code: code == UserRole.ADMIN or WorkerRole.objects.filter(code=code).exists(), "KASB"
    )
