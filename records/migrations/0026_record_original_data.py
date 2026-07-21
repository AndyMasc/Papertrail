from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0025_add_mergelog_trigram_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="record",
            name="original_data",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="record",
            name="original_plaid",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
