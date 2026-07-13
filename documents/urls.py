from django.urls import path

from . import views

app_name = "documents"
urlpatterns = [
    path("upload/", views.UploadView.as_view(), name="upload_document"),
    path("view/<int:pk>/", views.ViewDocument.as_view(), name="view_document"),
    path("delete/<int:pk>/", views.DeleteDocument.as_view(), name="delete_document"),
    path("add-supporting/<int:record_id>/", views.AddSupportDocuments.as_view(), name="add_support_docs"),
    path("document_lists/", views.DocumentListView.as_view(), name="document_list_view"),
]
