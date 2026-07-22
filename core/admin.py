from django.contrib import admin

from .models import UserSettings, Notification

admin.site.register(UserSettings)
admin.site.register(Notification)
