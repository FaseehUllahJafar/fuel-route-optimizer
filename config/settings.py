import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# LocMemCache is per process; use Redis or Memcached when running several workers.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-optimizer",
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"


# Fuel route planner

DATA_DIR = BASE_DIR / "data"
FUEL_PRICES_CSV = DATA_DIR / "fuel-prices-for-be-assessment.csv"
PLACES_CSV = DATA_DIR / "us_places.csv"
STATION_GEOCODE_OVERRIDES_CSV = DATA_DIR / "station_geocode_overrides.csv"

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
HTTP_USER_AGENT = os.environ.get("HTTP_USER_AGENT", "fuel-route-optimizer/1.0")
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 10)

VEHICLE_RANGE_MILES = env_float("VEHICLE_RANGE_MILES", 500)
VEHICLE_MPG = env_float("VEHICLE_MPG", 10)

# Station coordinates are city centroids, not exact exits, hence the generous corridor.
STATION_CORRIDOR_MILES = env_float("STATION_CORRIDOR_MILES", 15)
# The tank starts empty, so the first fill-up has to be close to the start.
FIRST_STOP_MAX_MILES = env_float("FIRST_STOP_MAX_MILES", 50)
# Planning cost of pulling over, so the plan doesn't stop to save a few cents.
# It is not included in the reported fuel cost.
FUEL_STOP_PENALTY_USD = env_float("FUEL_STOP_PENALTY_USD", 5)

ROUTE_CACHE_SECONDS = int(os.environ.get("ROUTE_CACHE_SECONDS", 6 * 3600))
