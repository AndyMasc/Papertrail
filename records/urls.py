from django.urls import path

from . import views

app_name = "records"
urlpatterns = [
    path("view_all_records/", views.RecordListView.as_view(), name="view_all_records"),
    path("add_record/<int:document_id>/", views.AddRecord.as_view(), name="add_record"),
    path("add_record/", views.AddRecord.as_view(), name="add_record_manual"), # add_record with no document_id. For manual record addition without a document or OCR.
    path("delete_record/<int:record_id>/", views.DeleteRecord.as_view(), name="delete_record"),
]
