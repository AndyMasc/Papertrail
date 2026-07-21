from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0025_documentdata_ocr_error"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentdata",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
