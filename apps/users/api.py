from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from rest_framework import generics, mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import User, all_role_choices, normalize_phone, worker_role_choices
from .permissions import IsWarehouseAdmin


class UserSerializer(serializers.ModelSerializer):
    role_labels = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_warehouse_admin = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "phone",
            "full_name",
            "roles",
            "role_labels",
            "is_warehouse_admin",
            "is_approved",
            "is_active",
            "date_joined",
        )
        read_only_fields = fields


class RegistrationSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=25)
    full_name = serializers.CharField(max_length=255)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    roles = serializers.MultipleChoiceField(choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].choices = worker_role_choices()  # trades live in the database

    def validate_phone(self, value):
        try:
            phone = normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        if User.objects.filter(phone=phone).exists():
            raise serializers.ValidationError(_("Bu raqam allaqachon ro'yxatdan o'tgan."))
        return phone

    def validate(self, attrs):
        candidate = User(username=attrs["phone"], phone=attrs["phone"], full_name=attrs["full_name"])
        try:
            password_validation.validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        return services.register_worker(
            phone=validated_data["phone"],
            password=validated_data["password"],
            full_name=validated_data["full_name"],
            roles=list(validated_data["roles"]),
        )


class RegistrationAPIView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "registration"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "detail": _("Ro'yxatdan o'tdingiz. Administrator tasdiqlagach tizimga kira olasiz."),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MeAPIView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ApprovalSerializer(serializers.Serializer):
    roles = serializers.MultipleChoiceField(choices=(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].choices = all_role_choices()


class PendingUserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Admin-only: review and approve/reject worker self-registrations."""

    serializer_class = UserSerializer
    permission_classes = [IsWarehouseAdmin]

    def get_queryset(self):
        return User.objects.pending_approval().order_by("date_joined")

    @action(detail=True, methods=["post"], serializer_class=ApprovalSerializer)
    def approve(self, request, pk=None):
        user = self.get_object()
        serializer = ApprovalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        roles = serializer.validated_data.get("roles")
        services.approve_user(user, approved_by=request.user, roles=list(roles) if roles else None)
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        services.reject_registration(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
