from django.urls import path

from . import views

app_name = "records"
urlpatterns = [
    path("view_all_records/", views.RecordListView.as_view(), name="view_all_records"),
    path("add_record/<int:document_id>/", views.AddRecordView.as_view(), name="add_record"),
    path("add_record/", views.AddRecordView.as_view(), name="add_record_manual"), # add_record with no document_id. For manual record addition without a document or OCR.
    path("delete_record/<int:pk>/", views.DeleteRecord.as_view(), name="delete_record"), # handles updating the record, as well as displaying it's details
    path("record_detail/<int:pk>/", views.RecordDetailView.as_view(), name="record_detail"),

    path("archive_record/<int:record_id>/", views.ArchiveRecord.as_view(), name="archive_record"),
    path("archive/", views.ArchiveRecord.as_view(), name="archive_view"),
    path("unarchive/<int:record_id>/", views.UnarchiveRecord.as_view(), name="unarchive_record"),

    path("check_ocr_status/<int:document_id>/", views.CheckOCRStatus.as_view(), name="check_ocr_status"),
]
