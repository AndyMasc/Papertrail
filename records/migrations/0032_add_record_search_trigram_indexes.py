from django.contrib.postgres.indexes import GinIndex
from django.db import migrations


class CreateRecordSearchTrigramIndexes(migrations.RunSQL):
    """Create pg_trgm GIN indexes on Record text fields (PostgreSQL only).

    These indexes accelerate the ``ILIKE '%…%'`` lookups used by
    ``RecordQuerySet.smart_search``.  On SQLite (dev) the operation is a
    no-op so the migration always succeeds.
    """

    _FIELDS = ("title", "merchant", "products", "notes")
    _SQL_TEMPLATE = (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
        "ON records_record USING gin ({field} gin_trgm_ops)"
    )
    _DROP_TEMPLATE = "DROP INDEX IF EXISTS {name};"

    def __init__(self):
        sql_parts = ["CREATE EXTENSION IF NOT EXISTS pg_trgm"]
        reverse_parts = []
        for field in self._FIELDS:
            name = f"idx_record_{field}_trgm"
            sql_parts.append(
                self._SQL_TEMPLATE.format(name=name, field=field)
            )
            reverse_parts.append(self._DROP_TEMPLATE.format(name=name))

        super().__init__(
            sql="; ".join(sql_parts),
            reverse_sql="; ".join(reverse_parts) if reverse_parts else migrations.RunSQL.noop,
        )

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql":
            return
        super().database_backwards(app_label, schema_editor, from_state, to_state)

    def describe(self):
        return "Create pg_trgm GIN indexes on Record text fields for search"


class Migration(migrations.Migration):
    """Add GIN trigram indexes for fast ILIKE search on Record text fields."""

    dependencies = [
        ("records", "0031_alter_historicalrecord_balance_and_more"),
    ]

    operations = [
        CreateRecordSearchTrigramIndexes(),
    ]
