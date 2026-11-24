import datetime as dt

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.permissions import CanTakeStock, IsWarehouseAdmin

from .. import selectors, services
from ..models import BaseMaterial, InventoryTransaction, TransactionType, category_codes
from .serializers import (
    BaseMaterialSerializer,
    KirimSerializer,
    LeftoverOffcutSerializer,
    OffcutWithdrawSerializer,
    TransactionSerializer,
    WholeMaterialSerializer,
    WithdrawSerializer,
)


def _search_params(request) -> dict:
    category = request.query_params.get("category", "")
    return {
        "query": request.query_params.get("q", "").strip(),
        "category": category if category in category_codes() else "",
    }


class MaterialViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Admin: material catalogue. Materials are archived (is_active=false), never deleted."""

    serializer_class = BaseMaterialSerializer
    permission_classes = [IsWarehouseAdmin]

    def get_queryset(self):
        return BaseMaterial.objects.select_related("whole_stock").order_by("category", "name")


class WholeMaterialViewSet(viewsets.ReadOnlyModelViewSet):
    """Whole materials (Butun materiallar), filtered by the caller's trade roles."""

    serializer_class = WholeMaterialSerializer

    def get_queryset(self):
        qs = selectors.visible_whole_stock(self.request.user)
        return selectors.search_stock(qs, **_search_params(self.request)).order_by("material__name")

    @action(
        detail=True,
        methods=["post"],
        serializer_class=WithdrawSerializer,
        permission_classes=[CanTakeStock],
    )
    def withdraw(self, request, pk=None):
        stock = self.get_object()
        payload = WithdrawSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        entry = services.withdraw_whole_material(material=stock.material, user=request.user, **payload.validated_data)
        return Response(TransactionSerializer(entry, context={"request": request}).data, status=status.HTTP_201_CREATED)


class LeftoverOffcutViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Leftover offcuts (Qoldiq materiallar). POST registers a new offcut."""

    serializer_class = LeftoverOffcutSerializer

    def get_queryset(self):
        qs = selectors.visible_offcuts(self.request.user)
        return selectors.search_stock(qs, **_search_params(self.request))

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        if "material_id" in getattr(serializer, "fields", {}):
            serializer.fields["material_id"].queryset = selectors.offcut_capable_materials(self.request.user)
        return serializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        offcut = services.register_offcut(
            material=data["material"],
            user=request.user,
            length_mm=data["length_mm"],
            width_mm=data["width_mm"],
            quantity=data.get("quantity", 1),
            notes=data.get("notes", ""),
        )
        return Response(self.get_serializer(offcut).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        serializer_class=OffcutWithdrawSerializer,
        permission_classes=[CanTakeStock],
    )
    def withdraw(self, request, pk=None):
        offcut = self.get_object()
        payload = OffcutWithdrawSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        entry = services.withdraw_offcut(offcut=offcut, user=request.user, **payload.validated_data)
        return Response(TransactionSerializer(entry, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Ledger (read-only). Admins see everything; workers see their own entries.

    Filters: ``month=YYYY-MM``, ``type``, ``category``, ``material``, ``user`` (admin).
    """

    serializer_class = TransactionSerializer

    def get_queryset(self):
        user = self.request.user
        params = self.request.query_params
        base = InventoryTransaction.objects.all() if user.is_warehouse_admin else InventoryTransaction.objects.filter(user=user)

        month = None
        if params.get("month"):
            try:
                month = dt.datetime.strptime(params["month"], "%Y-%m").date()
            except ValueError:
                month = None
        transaction_type = params.get("type", "")
        category = params.get("category", "")
        return selectors.filter_transactions(
            base,
            month=month,
            category=category if category in category_codes() else "",
            transaction_type=transaction_type if transaction_type in TransactionType.values else "",
            material_id=int(params["material"]) if params.get("material", "").isdigit() else None,
            user_id=int(params["user"]) if user.is_warehouse_admin and params.get("user", "").isdigit() else None,
        )


class KirimAPIView(APIView):
    """Admin: record a stock arrival. Send either unit_price_usd or total_price_usd."""

    permission_classes = [IsWarehouseAdmin]

    def post(self, request):
        payload = KirimSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        entry = services.record_kirim(user=request.user, **payload.validated_data)
        return Response(TransactionSerializer(entry, context={"request": request}).data, status=status.HTTP_201_CREATED)


class DashboardAPIView(APIView):
    """Admin: asset valuation, monthly Kirim vs Chiqim, and minimum-stock alerts."""

    permission_classes = [IsWarehouseAdmin]

    def get(self, request):
        flows = selectors.recent_month_flows(6)
        low_stock = selectors.low_stock_items()
        return Response(
            {
                "currency": "USD",
                "valuation_usd": str(selectors.warehouse_valuation_usd()),
                "current_month": {
                    "month": flows[-1].month.strftime("%Y-%m"),
                    "kirim_usd": str(flows[-1].kirim_usd),
                    "chiqim_usd": str(flows[-1].chiqim_usd),
                },
                "months": [
                    {
                        "month": flow.month.strftime("%Y-%m"),
                        "kirim_usd": str(flow.kirim_usd),
                        "chiqim_usd": str(flow.chiqim_usd),
                        "kirim_count": flow.kirim_count,
                        "chiqim_count": flow.chiqim_count,
                    }
                    for flow in flows
                ],
                "low_stock": WholeMaterialSerializer(low_stock, many=True, context={"request": request}).data,
            }
        )
