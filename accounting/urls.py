"""URL configuration for the accounting application.

All routes live under the ``accounting`` namespace. The app
allows for accounting integrations and export options.
"""

from django.urls import path

from . import views

app_name = "accounting"
urlpatterns = [
    path("export_excel/", views.ExportExcel, name="export_to_excel"),
]
