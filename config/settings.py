"""Django settings for the fuel route planner."""

import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

PRODUCTION = os.environ.get("DJANGO_ENV") == "production"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production")
    SECRET_KEY = secrets.token_urlsafe(50)

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(
        ","
    )
    if h.strip()
]
if PRODUCTION and (
    DEBUG or "*" in ALLOWED_HOSTS or not os.environ.get("DJANGO_ALLOWED_HOSTS")
):
    raise ImproperlyConfigured(
        "Production requires DEBUG=0 and explicit DJANGO_ALLOWED_HOSTS"
    )
SECURE_SSL_REDIRECT = PRODUCTION
SECURE_HSTS_SECONDS = 31536000 if PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUCTION
# OSM tiles require a Referer. Cross-origin requests expose only the site
# origin, keeping route queries and addresses out of that header.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 16_384

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "fuelapp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
