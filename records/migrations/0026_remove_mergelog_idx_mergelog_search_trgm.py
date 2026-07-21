from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("records", "0025_add_mergelog_trigram_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(
                    model_name="mergelog",
                    name="idx_mergelog_search_trgm",
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP INDEX IF EXISTS idx_mergelog_search_trgm;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
