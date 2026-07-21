from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0027_activitylog_and_more"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="ActivityLog",
            new_name="RecordEvent",
        ),
        migrations.RenameIndex(
            model_name="recordevent",
            old_name="records_act_record__691744_idx",
            new_name="records_rec_record__261d02_idx",
        ),
        migrations.AddField(
            model_name="record",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
