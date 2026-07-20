from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations
from django.db.models import F


class CreateMergeLogTrigIndex(migrations.AddIndex):
    """Creates pg_trgm extension and GIN trigram index (PostgreSQL only)."""

    def __init__(self):
        index = GinIndex(
            OpClass(F("search_text"), name="gin_trgm_ops"),
            name="idx_mergelog_search_trgm",
        )
        super().__init__(model_name="mergelog", index=index)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        schema_editor.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mergelog_search_trgm "
            "ON records_mergelog USING gin (search_text gin_trgm_ops)"
        )

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        schema_editor.execute("DROP INDEX IF EXISTS idx_mergelog_search_trgm")

    def describe(self):
        return "Create pg_trgm extension and GIN trigram index on MergeLog.search_text"


class Migration(migrations.Migration):
    """Add GIN trigram index for full-text search on MergeLog."""

    dependencies = [
        ("records", "0024_add_mergelog_search_text"),
    ]

    operations = [
        CreateMergeLogTrigIndex(),
    ]
