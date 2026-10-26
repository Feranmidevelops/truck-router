from pathlib import Path
import os
import tempfile

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
# Off unless asked for: a debug error page lists the failing code's local variables, the TomTom key
# among them. backend/.env.example turns it on for local work.
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    # Above CommonMiddleware so it compresses the final body. A plan is ~100 kB of JSON, ~20 kB gzipped.
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

# No database: the app is stateless. Plans and weather are kept in the cache.
DATABASES: dict = {}

# File cache so every gunicorn worker sees the same plans (the heatmap request
# can land on a different worker than the plan request). Locmem would not.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.environ.get("CACHE_DIR", str(Path(tempfile.gettempdir()) / "truck-routing-cache")),
        "TIMEOUT": 60 * 60,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # Limits per client IP, for views that set throttle_scope (health doesn't). History lives in
    # the shared cache so all gunicorn workers count together. The IP is REMOTE_ADDR, or with
    # NUM_PROXIES > 0 the X-Forwarded-For entry that many places from the end.
    "NUM_PROXIES": env_int("NUM_PROXIES", 0),
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"plan": "10/min", "geocode": "120/min", "heatmap": "30/min"},
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django's default console handler only prints with DEBUG on, so with DEBUG off a 500 left no
# trace in Render's log. Everything goes up to one console handler on the root logger instead.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"level": "WARNING"},
        "django.request": {"level": "ERROR"},  # 5xx only; 4xx are bad input or throttling
        "routing": {"level": "INFO"},
    },
}

# App settings
TOMTOM_API_KEY = os.environ.get("TOMTOM_API_KEY", "")
GEOCODE_COUNTRY_SET = os.environ.get("GEOCODE_COUNTRY_SET", "US")
# Serve canned responses from routing/fixtures so the UI can be built without API keys.
USE_FIXTURES = env_bool("USE_FIXTURES", True)
