from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

from .services import export_to_excel


@login_required
def ExportExcel(request):
    excel_data = export_to_excel()

    response = HttpResponse(
        excel_data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="Record_export.xlsx"'
    return response
