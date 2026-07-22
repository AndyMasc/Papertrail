from django.contrib import admin

from .models import Notification, UserSettings

admin.site.register(UserSettings)
admin.site.register(Notification)
