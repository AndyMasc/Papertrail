from django.contrib import admin

from .models import Folder, MergeLog, Record, RecordEvent


class RecordAdmin(admin.ModelAdmin):
    list_display = ["title", "user", "source_type", "record_type", "is_active", "date_added"]
    list_filter = ["source_type", "record_type", "is_active"]


admin.site.register(Record, RecordAdmin)
admin.site.register(Folder)
admin.site.register(MergeLog)
admin.site.register(RecordEvent)
