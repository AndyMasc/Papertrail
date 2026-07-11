from .base import *

# Dev-only apps and middleware
INSTALLED_APPS += ["django_browser_reload"]
MIDDLEWARE += ["django_browser_reload.middleware.BrowserReloadMiddleware"]