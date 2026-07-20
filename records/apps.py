from django.apps import AppConfig


class RecordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "records"

    def ready(self):
        import records.signals  # noqa: F401
