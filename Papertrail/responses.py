import json

from django.http import HttpRequest, HttpResponse, JsonResponse


def api_error(
    request: HttpRequest,
    message: str,
    code: str = "error",
    status: int = 400,
    details: dict | None = None,
) -> HttpResponse:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(json.dumps(body), status=status, content_type="application/json")
        response["HX-Trigger"] = json.dumps(
            {"showToast": {"text": message, "tags": "error"}}
        )
        return response

    return JsonResponse(body, status=status)


def api_success(
    request: HttpRequest,
    message: str,
    data: dict | None = None,
    status: int = 200,
) -> HttpResponse:
    body = {"success": message}
    if data:
        body["data"] = data

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(json.dumps(body), status=status, content_type="application/json")
        response["HX-Trigger"] = json.dumps(
            {"showToast": {"text": message, "tags": "success"}}
        )
        return response

    return JsonResponse(body, status=status)
