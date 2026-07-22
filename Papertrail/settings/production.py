from typing import Any

from pythonjsonlogger.json import JsonFormatter

ALLOWED_HOSTS: list[str] = []

CSRF_TRUSTED_ORIGINS: list[str] = []
CORS_ALLOW_ALL_ORIGINS = False

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "core.middleware.RequestIDLogFilter",
        },
    },
    "formatters": {
        "json": {
            "()": JsonFormatter,
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s %(module)s %(process)d %(thread)d %(request_id)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "documents": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "records": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
