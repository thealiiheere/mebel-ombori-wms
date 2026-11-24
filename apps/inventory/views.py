import csv
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, FormView, ListView, TemplateView, UpdateView

from apps.users.permissions import (
    ApprovedUserRequiredMixin,
    StockTakerRequiredMixin,
    WarehouseAdminRequiredMixin,
)

from . import selectors, services
from .templatetags.wms import month_name
from .forms import (
    KirimForm,
    MaterialForm,
    OffcutForm,
    ReportFilterForm,
    WithdrawOffcutForm,
    WithdrawWholeForm,
)
from .models import (
    BaseMaterial,
    InventoryTransaction,
    LeftoverOffcut,
    category_choices,
    category_codes,
    default_roles_for_category,
    transaction_type_label,
)


def apply_service_errors(form, exc: ValidationError) -> None:
    """Attach a service-layer ValidationError to the matching form fields."""
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            target = field if field in form.fields else None
            for error in errors:
                form.add_error(target, error)
    else:
        for message in exc.messages:
            form.add_error(None, message)


def fmt_qty(value: Decimal) -> str:
    return f"{Decimal(value).normalize():f}"


# ---------------------------------------------------------------------------
# Worker-facing inventory
# ---------------------------------------------------------------------------
class HomeView(ApprovedUserRequiredMixin, TemplateView):
    """Tabbed inventory: whole materials (top) and leftover offcuts (bottom)."""

    template_name = "inventory/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        query = self.request.GET.get("q", "").strip()
        category = self.request.GET.get("category", "")
        if category not in category_codes():
            category = ""
        tab = self.request.GET.get("tab", "whole")

        whole = selectors.search_stock(selectors.visible_whole_stock(user), query=query, category=category)
        offcuts = selectors.search_stock(selectors.visible_offcuts(user), query=query, category=category)
        visible_categories = set(selectors.visible_materials(user).values_list("category", flat=True).distinct())

        context.update(
            {
                "whole_stock": whole.order_by("material__category", "material__name"),
                "offcuts": offcuts,
                "query": query,
                "category": category,
                "tab": tab if tab in ("whole", "offcuts") else "whole",
                "categories": [(code, name) for code, name in category_choices() if code in visible_categories],
                "can_add_offcut": selectors.offcut_capable_materials(user).exists(),
            }
        )
        return context


class WithdrawWholeView(StockTakerRequiredMixin, FormView):
    """'Oldim' — worker takes whole units off the shelf."""

    form_class = WithdrawWholeForm
    template_name = "inventory/withdraw_whole.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.stock = get_object_or_404(selectors.visible_whole_stock(request.user), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "stock": self.stock}

    def get_context_data(self, **kwargs):
        return super().get_context_data(stock=self.stock, **kwargs)

    def form_valid(self, form):
        try:
            entry = services.withdraw_whole_material(
                material=self.stock.material,
                user=self.request.user,
                quantity=form.cleaned_data["quantity"],
                notes=form.cleaned_data.get("notes", ""),
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
            self.stock.refresh_from_db()
            return self.form_invalid(form)
        unit = self.stock.material.unit_short
        messages.success(
            self.request,
            _("Chiqim yozildi: %(name)s — %(amount)s %(unit)s. Omborda qoldi: %(left)s %(unit)s.") % {
                "name": self.stock.material.name,
                "amount": fmt_qty(entry.quantity_abs),
                "left": fmt_qty(entry.stock_after),
                "unit": unit,
            },
        )
        return redirect(reverse("inventory:home") + "?tab=whole")


class OffcutCreateView(ApprovedUserRequiredMixin, FormView):
    """'Qoldiq qo'shish' — log a reusable leftover piece."""

    form_class = OffcutForm
    template_name = "inventory/offcut_form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def get_initial(self):
        initial = super().get_initial()
        material_id = self.request.GET.get("material")
        if material_id and material_id.isdigit():
            initial["material"] = int(material_id)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        context["sheet_sizes"] = {
            str(m.pk): [m.whole_stock.length_mm, m.whole_stock.width_mm]
            for m in form.fields["material"].queryset
            if hasattr(m, "whole_stock") and m.whole_stock.length_mm
        }
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            offcut = services.register_offcut(
                material=data["material"],
                user=self.request.user,
                length_mm=data["length_mm"],
                width_mm=data["width_mm"],
                quantity=data["quantity"],
                notes=data.get("notes", ""),
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Qoldiq qo'shildi: %(name)s %(length)s×%(width)s mm — %(count)s dona.") % {
                "name": offcut.material.name,
                "length": offcut.length_mm,
                "width": offcut.width_mm,
                "count": offcut.quantity,
            },
        )
        return redirect(reverse("inventory:home") + "?tab=offcuts")


class OffcutWithdrawView(StockTakerRequiredMixin, FormView):
    form_class = WithdrawOffcutForm
    template_name = "inventory/withdraw_offcut.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.offcut = get_object_or_404(selectors.visible_offcuts(request.user), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "max_quantity": self.offcut.quantity}

    def get_context_data(self, **kwargs):
        return super().get_context_data(offcut=self.offcut, **kwargs)

    def form_valid(self, form):
        try:
            services.withdraw_offcut(
                offcut=self.offcut,
                user=self.request.user,
                quantity=form.cleaned_data["quantity"],
                notes=form.cleaned_data.get("notes", ""),
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Qoldiq olindi: %(name)s %(length)s×%(width)s mm — %(count)s dona.") % {
                "name": self.offcut.material.name,
                "length": self.offcut.length_mm,
                "width": self.offcut.width_mm,
                "count": form.cleaned_data["quantity"],
            },
        )
        return redirect(reverse("inventory:home") + "?tab=offcuts")


class MyHistoryView(ApprovedUserRequiredMixin, ListView):
    template_name = "inventory/my_history.html"
    context_object_name = "transactions"
    paginate_by = 30

    def get_queryset(self):
        return InventoryTransaction.objects.filter(user=self.request.user).select_related("material")


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
class DashboardView(WarehouseAdminRequiredMixin, TemplateView):
    template_name = "inventory/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        flows = selectors.recent_month_flows(6, today=today)
        current = flows[-1]
        previous = flows[-2] if len(flows) > 1 else None
        peak = max([max(f.kirim_usd, f.chiqim_usd) for f in flows] + [Decimal("0")])
        context.update(
            {
                "valuation_usd": selectors.warehouse_valuation_usd(),
                "current_flow": current,
                "previous_flow": previous,
                "month_flows": flows,
                "flow_peak": peak,
                "flow_chart": {
                    "labels": [month_name(f.month).capitalize() for f in flows],
                    "up": [float(f.kirim_usd) for f in flows],
                    "down": [float(f.chiqim_usd) for f in flows],
                },
                "low_stock": selectors.low_stock_items(),
                "recent_transactions": InventoryTransaction.objects.select_related("material", "user")[:10],
                "material_count": BaseMaterial.objects.filter(is_active=True).count(),
                "offcut_count": LeftoverOffcut.objects.filter(material__is_active=True).count(),
                "offcut_value_usd": selectors.estimated_offcut_value_usd(),
            }
        )
        return context


class MaterialListView(WarehouseAdminRequiredMixin, ListView):
    template_name = "inventory/material_list.html"
    context_object_name = "materials"
    paginate_by = 50

    def get_queryset(self):
        qs = BaseMaterial.objects.select_related("whole_stock").order_by("-is_active", "category", "name")
        query = self.request.GET.get("q", "").strip()
        category = self.request.GET.get("category", "")
        status = self.request.GET.get("status", "active")
        if query:
            qs = qs.filter(name__icontains=query)
        if category in category_codes():
            qs = qs.filter(category=category)
        if status == "active":
            qs = qs.filter(is_active=True)
        elif status == "archived":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "query": self.request.GET.get("q", ""),
                "category": self.request.GET.get("category", ""),
                "status": self.request.GET.get("status", "active"),
                "categories": category_choices(),
            }
        )
        return context


class MaterialCreateView(WarehouseAdminRequiredMixin, CreateView):
    form_class = MaterialForm
    template_name = "inventory/material_form.html"

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            with transaction.atomic():
                form.create_pending_category()
                material = services.create_material(
                    name=data["name"],
                    category=data["category"],
                    unit_of_measure=data["unit_of_measure"],
                    allowed_roles=data.get("allowed_roles"),
                    container_size=data.get("container_size"),
                    minimum_threshold=data.get("minimum_threshold") or Decimal("0"),
                    length_mm=data.get("length_mm"),
                    width_mm=data.get("width_mm"),
                )
        except ValidationError as exc:
            apply_service_errors(form, exc)
            return self.form_invalid(form)
        messages.success(self.request, _("“%(name)s” qo'shildi. Endi kirimni kiriting.") % {"name": material.name})
        return redirect(reverse("inventory:kirim") + f"?material={material.pk}")


class MaterialUpdateView(WarehouseAdminRequiredMixin, UpdateView):
    model = BaseMaterial
    form_class = MaterialForm
    template_name = "inventory/material_form.html"
    context_object_name = "material"

    def form_valid(self, form):
        material = form.save(commit=False)
        if not material.allowed_roles:
            material.allowed_roles = default_roles_for_category(material.category)
        with transaction.atomic():
            form.create_pending_category()
            material.save()
            form.save_stock_dimensions(material)
        messages.success(self.request, _("“%(name)s” saqlandi.") % {"name": material.name})
        return redirect("inventory:material_list")


class KirimView(WarehouseAdminRequiredMixin, FormView):
    """Kirim — stock arrival entry."""

    form_class = KirimForm
    template_name = "inventory/kirim_form.html"

    def get_initial(self):
        initial = super().get_initial()
        material_id = self.request.GET.get("material")
        if material_id and material_id.isdigit():
            initial["material"] = int(material_id)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        materials = context["form"].fields["material"].queryset.select_related("whole_stock")
        context["material_meta"] = {
            str(m.pk): {
                "unit": m.unit_short,
                "container": str(m.container_size) if m.container_size else None,
                "integer": m.requires_integer_quantity,
                "price": str(m.unit_price_usd),
                "stock": str(getattr(getattr(m, "whole_stock", None), "quantity", "0")),
            }
            for m in materials
        }
        context["recent_kirim"] = (
            InventoryTransaction.objects.kirim().select_related("material", "user")[:8]
        )
        return context

    def form_valid(self, form):
        try:
            entry = services.record_kirim(user=self.request.user, **form.service_kwargs())
        except ValidationError as exc:
            apply_service_errors(form, exc)
            return self.form_invalid(form)
        messages.success(
            self.request,
            _("Kirim yozildi: %(name)s +%(amount)s %(unit)s, %(total)s. Yangi o'rtacha narx: %(price)s.") % {
                "name": entry.material.name,
                "amount": fmt_qty(entry.quantity_changed),
                "unit": entry.material.unit_short,
                "total": f"${entry.total_price_usd:,.2f}",
                "price": f"${entry.material.unit_price_usd:,.4f}",
            },
        )
        return redirect("inventory:kirim")


class ReportView(WarehouseAdminRequiredMixin, ListView):
    """Monthly consumption history with filters by month, category, worker and material."""

    template_name = "inventory/reports.html"
    context_object_name = "transactions"
    paginate_by = 50

    def get_filter_form(self) -> ReportFilterForm:
        data = self.request.GET.copy()
        if not any(data.get(key) for key in ReportFilterForm.base_fields):
            data["month"] = timezone.localdate().strftime("%Y-%m")
        return ReportFilterForm(data)

    def get_queryset(self):
        self.filter_form = self.get_filter_form()
        return selectors.filter_transactions(**self.filter_form.selector_kwargs())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.object_list
        query_string = self.request.GET.copy()
        query_string.pop("page", None)
        context.update(
            {
                "filter_form": self.filter_form,
                "totals": selectors.transaction_totals(qs),
                "by_material": selectors.consumption_by_material(qs)[:20],
                "by_worker": selectors.consumption_by_worker(qs)[:20],
                "query_string": query_string.urlencode(),
                "export_query": self.filter_form.data.urlencode(),
            }
        )
        return context


def _csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection in free-text cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class ReportExportView(WarehouseAdminRequiredMixin, View):
    def get(self, request):
        form = ReportFilterForm(request.GET)
        qs = selectors.filter_transactions(**form.selector_kwargs())
        month = form.cleaned_data.get("month") if form.is_valid() else None
        filename = f"ombor-harakatlari-{month:%Y-%m}.csv" if month else "ombor-harakatlari.csv"

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response.write("﻿")  # BOM so Excel opens UTF-8 correctly
        writer = csv.writer(response)
        writer.writerow(
            [
                _("Sana"),
                _("Turi"),
                _("Material"),
                _("Kategoriya"),
                _("Miqdor"),
                _("Birlik"),
                _("Birlik narxi (USD)"),
                _("Jami (USD)"),
                _("Qoldiq o'lchami (mm)"),
                _("Ishchi"),
                _("Izoh"),
            ]
        )
        for txn in qs.iterator(chunk_size=500):
            writer.writerow(
                [
                    timezone.localtime(txn.created_at).strftime("%Y-%m-%d %H:%M"),
                    transaction_type_label(txn.transaction_type),
                    _csv_safe(txn.material.name),
                    _csv_safe(txn.material.category_short),
                    f"{txn.quantity_changed}",
                    txn.material.unit_short,
                    f"{txn.unit_price_usd}",
                    f"{txn.total_price_usd}",
                    f"{txn.offcut_length_mm}x{txn.offcut_width_mm}" if txn.is_offcut else "",
                    _csv_safe(txn.user.display_name),
                    _csv_safe(txn.notes),
                ]
            )
        return response

