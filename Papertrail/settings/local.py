from .base import (  # noqa: F403,F401
    INSTALLED_APPS,
    MIDDLEWARE,
    STORAGES,
)

INSTALLED_APPS.append("django_browser_reload")
MIDDLEWARE.insert(-1, "django_browser_reload.middleware.BrowserReloadMiddleware")

CSRF_TRUSTED_ORIGINS = ["https://*.ngrok-free.app"]
CORS_ALLOW_ALL_ORIGINS = True
ALLOWED_HOSTS = ["*"]

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Use simple static files storage in dev (no manifest required)
STORAGES = {
    "default": STORAGES["default"],
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Disable CSP in dev
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": (
            "'self'",
            "'unsafe-inline'",
            "'unsafe-eval'",
            "data:",
            "blob:",
            "https:",
            "http:",
        ),
        "script-src": ("'self'", "'unsafe-inline'", "'unsafe-eval'", "https:", "http:"),
        "style-src": ("'self'", "'unsafe-inline'", "https:", "http:"),
        "img-src": ("'self'", "data:", "blob:", "https:", "http:"),
        "connect-src": ("'self'", "https:", "http:", "ws:", "wss:"),
        "font-src": ("'self'", "https:", "http:", "data:"),
        "frame-ancestors": ("'none'",),
    }
}
