import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY to a securely generated value.")
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
CSRF_TRUSTED_ORIGINS = list(filter(None, os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")))
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "weisswurstrunde.apps.WeisswurstrundeConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "weisswurstrunde.middleware.PrivatePagesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
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
            ]
        },
    }
]
DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["OPTIONS"] = {"timeout": 20, "transaction_mode": "IMMEDIATE"}
AUTH_USER_MODEL = "weisswurstrunde.User"
AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "de-de"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
if os.environ.get("TRUST_PROXY", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
INVITATION_CODE = os.environ.get("INVITATION_CODE", "")
WEISSWURST_WEEKDAY = int(os.environ.get("WEISSWURST_WEEKDAY", "3"))
LEBERKAESE_WEEKDAY = int(os.environ.get("LEBERKAESE_WEEKDAY", "4"))
DEADLINE_DAYS_BEFORE = int(os.environ.get("DEADLINE_DAYS_BEFORE", "1"))
DEADLINE_TIME = os.environ.get("DEADLINE_TIME", "18:00")
UPCOMING_WEEKS = max(8, int(os.environ.get("UPCOMING_WEEKS", "8")))
PAYPAL_ME_LINK = os.environ.get("PAYPAL_ME_LINK", "")
PAYPAL_IMAP_HOST = os.environ.get("PAYPAL_IMAP_HOST", "")
PAYPAL_IMAP_PORT = int(os.environ.get("PAYPAL_IMAP_PORT", "993"))
PAYPAL_IMAP_USER = os.environ.get("PAYPAL_IMAP_USER", "")
PAYPAL_IMAP_PASSWORD = os.environ.get("PAYPAL_IMAP_PASSWORD", "")
PAYPAL_IMAP_FOLDER = os.environ.get("PAYPAL_IMAP_FOLDER", "INBOX")
PAYPAL_IMAP_USE_TLS = os.environ.get("PAYPAL_IMAP_USE_TLS", "false").lower() == "true"
PAYPAL_IMAP_USE_SSL = os.environ.get("PAYPAL_IMAP_USE_SSL", "true").lower() == "true"
if PAYPAL_IMAP_USE_TLS and PAYPAL_IMAP_USE_SSL:
    raise ImproperlyConfigured("PAYPAL_IMAP_USE_TLS and PAYPAL_IMAP_USE_SSL cannot both be true.")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() == "true"
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be true.")
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "weisswurstrunde@localhost")
AUDIT_LOG_FILE = os.environ.get("AUDIT_LOG_FILE", str(BASE_DIR / "audit.log"))
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "human": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
        "file": {
            "class": "logging.FileHandler",
            "filename": AUDIT_LOG_FILE,
            "encoding": "utf-8",
            "formatter": "human",
        },
    },
    "loggers": {
        "weisswurstrunde": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "weisswurstrunde.audit": {
            "handlers": ["file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
