from django.http import HttpRequest, HttpResponse

from records.models import Record


def archive_record(record: Record, request: HttpRequest | None = None) -> HttpResponse | None:
    record.is_active = False
    record.save(update_fields=["is_active"])

    if request and request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Trigger"] = "recordChanged"
        return response

    return None


def unarchive_record(record: Record) -> None:
    record.is_active = True
    record.save(update_fields=["is_active"])
