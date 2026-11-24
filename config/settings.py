"""
Django settings for the Furniture Workshop WMS.

All environment-specific values come from environment variables (optionally
loaded from a local ``.env`` file). See ``.env.example`` for the full list.
"""

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


def parse_database_url(url: str) -> dict:
    """Translate a DATABASE_URL into a Django DATABASES entry."""
    parsed = urlparse(url)
    if parsed.scheme in {"postgres", "postgresql", "pgsql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
        }
    if parsed.scheme == "sqlite":
        name = url[len("sqlite:///"):] or "db.sqlite3"
        path = Path(name)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path if path.is_absolute() else BASE_DIR / path,
        }
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")


TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "django-insecure-local-development-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "apps.users",
    "apps.inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.users.middleware.CookieLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.users.middleware.RoleCacheMiddleware",
    "apps.inventory.middleware.CategoryCacheMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.inventory.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database (PostgreSQL in production; SQLite fallback for quick local runs)
# ---------------------------------------------------------------------------
DATABASES = {"default": parse_database_url(env("DATABASE_URL", "sqlite:///db.sqlite3"))}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "users.User"
AUTHENTICATION_BACKENDS = ["apps.users.backends.ApprovedUserBackend"]

LOGIN_URL = "users:login"
LOGIN_REDIRECT_URL = "inventory:home"
LOGOUT_REDIRECT_URL = "users:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # two weeks: workshop phones stay logged in
SESSION_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# Internationalisation: Uzbek (Latin, the source language of all UI strings),
# Russian and English. Users switch with the language menu; the choice is kept in
# a cookie. Money is always USD. Translations live in locale/ (see scripts/i18n.py).
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "uz"
LANGUAGES = [
    ("uz", "O'zbekcha"),
    ("ru", "Русский"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Tashkent")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files (compiled Tailwind CSS + vendored Alpine.js, served by WhiteNoise)
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["apps.users.permissions.IsApprovedUser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": (
        ["rest_framework.renderers.JSONRenderer", "rest_framework.renderers.BrowsableAPIRenderer"]
        if DEBUG
        else ["rest_framework.renderers.JSONRenderer"]
    ),
    "DEFAULT_THROTTLE_RATES": {"registration": env("REGISTRATION_THROTTLE_RATE", "10/hour")},
    "EXCEPTION_HANDLER": "apps.inventory.api.exceptions.exception_handler",
    "COERCE_DECIMAL_TO_STRING": True,
}

# Branding shown in the header / page titles.
WMS_BRAND_NAME = env("WMS_BRAND_NAME", "Mebel Ombori")

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if not DEBUG and not TESTING:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    # Browsers never send Secure cookies over plain HTTP, so login would fail on an
    # HTTP-only deployment (e.g. a bare IP). Turn this off only in that case.
    SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
    SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
    if env_bool("DJANGO_BEHIND_PROXY", False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
        USE_X_FORWARDED_HOST = True

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        # Tests deliberately trigger 403/404s; keep their output readable.
        "django.request": {"level": "ERROR" if TESTING else "WARNING"},
        "wms.security": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
