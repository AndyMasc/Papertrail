from django.conf import settings
from django.contrib import admin
from django.http import HttpResponseForbidden
from django.urls import include, path

from core.views import safe_webpush_save_info


def forbidden_view(request, *args, **kwargs):  # noqa: ARG001
    return HttpResponseForbidden("Password features are disabled.")


urlpatterns = [
    # Landing page
    path("", include("core.urls")),
    # Admin URLs
    path("admin/", admin.site.urls),
    path("qstash/webhook/", include("django_qstash.urls")),
    # Block password management paths completely
    path("accounts/password/change/", forbidden_view),
    path("accounts/password/set/", forbidden_view),
    path("accounts/password/reset/", forbidden_view),
    # Include allauth normally for everything else
    path("accounts/", include("allauth.urls")),
    path("documents/", include("documents.urls")),
    path("records/", include("records.urls")),
    # Webpush
    path(
        "webpush/save_information", safe_webpush_save_info, name="save_webpush_info"
    ),  # Custom URL to catch webpush POST before sent to fix webpush MultipleObjectsReturned error.
    path("webpush/", include("webpush.urls")),
]

if settings.DEBUG:
    urlpatterns.insert(3, path("__reload__/", include("django_browser_reload.urls")))
