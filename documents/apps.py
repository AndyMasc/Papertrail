from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    name = "documents"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401
