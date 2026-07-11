from .base import *

# By default, use local settings. Override in production by setting an environment variable 'DJANGO_ENV'
import os

if os.environ.get("DJANGO_ENV") == "production":
    from .production import *
else:
    from .local import *