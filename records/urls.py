from django.urls import path

from . import views

app_name = "records"
urlpatterns = [
    path("view_all_records/", views.RecordListView.as_view(), name="view_all_records"),
    path("add_record/", views.AddRecord.as_view(), name="add_record"),
]
