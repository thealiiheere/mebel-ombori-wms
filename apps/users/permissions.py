"""Role-based access control for both the HTML views and the DRF API."""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission


def is_approved_user(user) -> bool:
    return bool(user and user.is_authenticated and user.can_use_system)


def is_warehouse_admin(user) -> bool:
    return is_approved_user(user) and user.is_warehouse_admin


def can_take_stock(user) -> bool:
    """Who may take material off the shelf: approved workers, never administrators."""
    return is_approved_user(user) and not user.is_warehouse_admin


# --- Django class-based view mixins -----------------------------------------
class ApprovedUserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Any approved worker or admin."""

    def test_func(self) -> bool:
        return is_approved_user(self.request.user)


class WarehouseAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Administrators only (ADMIN role or superuser)."""

    def test_func(self) -> bool:
        return is_warehouse_admin(self.request.user)


class StockTakerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Workers only: the withdrawal screens are closed to administrators."""

    def test_func(self) -> bool:
        return can_take_stock(self.request.user)


# --- DRF permission classes ----------------------------------------------------
class IsApprovedUser(BasePermission):
    message = _("Hisobingiz administrator tomonidan tasdiqlanmagan.")

    def has_permission(self, request, view) -> bool:
        return is_approved_user(request.user)


class IsWarehouseAdmin(BasePermission):
    message = _("Bu amal faqat administratorlar uchun.")

    def has_permission(self, request, view) -> bool:
        return is_warehouse_admin(request.user)


class CanTakeStock(BasePermission):
    message = _("Administrator ombordan material olmaydi — buni ishchi bajaradi.")

    def has_permission(self, request, view) -> bool:
        return can_take_stock(request.user)
