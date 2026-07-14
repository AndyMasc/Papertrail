from .base import INSTALLED_APPS, MIDDLEWARE, ALLOWED_HOSTS

INSTALLED_APPS.append("django_browser_reload")
MIDDLEWARE.append("django_browser_reload.middleware.BrowserReloadMiddleware")

CSRF_TRUSTED_ORIGINS = ["https://*.ngrok-free.app"]
CORS_ALLOW_ALL_ORIGINS = True
ALLOWED_HOSTS.append("*")
