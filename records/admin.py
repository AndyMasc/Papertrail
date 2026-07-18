from django.contrib import admin

from .models import Folder, Record

admin.site.register(Record)
admin.site.register(Folder)
