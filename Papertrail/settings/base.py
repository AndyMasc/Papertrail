from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"

if env_file.exists():
    env.read_env(str(env_file))
else:
    print(f"Warning: .env file not found at {env_file}")

# Core
SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
SITE_ID = 1
ROOT_URLCONF = "Papertrail.urls"
WSGI_APPLICATION = "Papertrail.wsgi.application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Apps
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    
    # Django QStash
    "django_qstash",
    "django_qstash.results",
    "django_qstash.schedules",
    
    # Allauth apps
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth_ui",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    
    # Customization apps
    "tailwind",
    "theme",
    "widget_tweaks",
    "slippers",
    "django_filters",
    
    # Local apps
    "core.apps.CoreConfig",
    "documents.apps.DocumentsConfig",
    "records.apps.RecordsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.TimezoneMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

# Auth & Allauth
ACCOUNT_ADAPTER = "core.adapters.QStashEmailAdapter"
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND = True
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_MAX_EMAIL_ADDRESSES = 3
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
LOGIN_REDIRECT_URL = "core:dashboard"
ACCOUNT_SIGNUP_REDIRECT_URL = "core:dashboard"
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_FORMS = {
    "signup": "core.forms.PasswordlessSignupForm",  # custom signup form to allow exclusively email only signups
    "login": "core.forms.PasswordlessLoginForm",  # custom login form to remove password field
}
ACCOUNT_RATE_LIMITS = {
    "login": "3/m/ip",  # 3 attempts per minute
    "login_failed": "3/5m/ip",  # 3 failures locks the IP out for 5 minutes ('5m')
}
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_OAUTH_CLIENT_ID"),
            "secret": env("GOOGLE_OAUTH_CLIENT_SECRET"),
            "key": "",
        },
    },
    "github": {
        "APP": {
            "client_id": env("GITHUB_OAUTH_CLIENT_ID"),
            "secret": env("GITHUB_OAUTH_CLIENT_SECRET"),
            "key": "",
        },
    },
}
AUTHENTICATION_BACKENDS = [
    # Needed to login by username in Django admin, regardless of `allauth`
    "django.contrib.auth.backends.ModelBackend",
    # `allauth` specific authentication methods, such as login by email
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Password validation - https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Cache & Session
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    },
}
SESSION_ENGINE = 'django.contrib.sessions.backends.db'#"django.contrib.sessions.backends.cached_db"

# Email
EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
ANYMAIL = {"RESEND_API_KEY": env("RESEND_API_KEY")}
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="Papertrail <onboarding@resend.dev>"
)
# EMAIL_HOST = env("EMAIL_HOST")
# EMAIL_PORT = int(env("EMAIL_PORT"))
# EMAIL_USE_TLS = env("EMAIL_USE_TLS") == "True"
# EMAIL_HOST_USER = env("EMAIL_HOST_USER")
# EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")

# Storage (S3/R2)- Uploads use signed urls in Cloudflare R2
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY")
R2_STORAGE_BUCKET_NAME = env("R2_STORAGE_BUCKET_NAME")
R2_S3_ENDPOINT_URL = env("R2_S3_ENDPOINT_URL")
R2_PAPERTRAIL_STORAGE_ACCOUNT_ID = env("R2_PAPERTRAIL_STORAGE_ACCOUNT_ID")

AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
AWS_STORAGE_BUCKET_NAME = R2_STORAGE_BUCKET_NAME
AWS_S3_ENDPOINT_URL = R2_S3_ENDPOINT_URL
AWS_S3_REGION_NAME = "auto"
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# AI / OCR
GEMINI_API_KEY = env("GEMINI_API_KEY")

# QStash
QSTASH_TOKEN = env("QSTASH_TOKEN")
DJANGO_QSTASH_DOMAIN = env("DJANGO_QSTASH_DOMAIN")
DJANGO_QSTASH_WEBHOOK_PATH = env("DJANGO_QSTASH_WEBHOOK_PATH")
QSTASH_CURRENT_SIGNING_KEY = env("QSTASH_CURRENT_SIGNING_KEY")
QSTASH_NEXT_SIGNING_KEY = env("QSTASH_NEXT_SIGNING_KEY")

# Internationalization & Static
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_URL = "static/"

TAILWIND_APP_NAME = "theme"
TAILWIND_USE_STANDALONE_BINARY = True
SITE_ID = 1  # Ensure this matches the ID of your site in the admin

# List view pagination
PAGINATE_BY = 25