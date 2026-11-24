from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class ApprovedUserBackend(ModelBackend):
    """
    Authenticates an approved user by **phone number**, falling back to the username.

    The credential the login form sends is whatever the person typed, normalised to
    ``+998XXXXXXXXX`` when it looks like a phone number. Accounts made with
    ``createsuperuser`` have no phone, so their username still works and nobody can
    be locked out of a fresh install.

    ``user_can_authenticate`` is consulted both at login time and every time a
    session is resolved (``get_user``), so revoking approval locks a worker out
    on their very next request, for HTML views and the API alike.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or password is None:
            return None

        # The phone wins over a same-looking username, and only a non-empty phone matches.
        candidates = list(
            UserModel._default_manager.filter(Q(phone=username) | Q(username=username))[:2]
        )
        user = next((u for u in candidates if u.phone == username), None) or next(iter(candidates), None)
        if user is None:
            # Run the default hasher once so a missing account costs the same as a wrong password.
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def user_can_authenticate(self, user) -> bool:
        if not super().user_can_authenticate(user):
            return False
        return bool(user.is_superuser or getattr(user, "is_approved", False))
