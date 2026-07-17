from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
from django.contrib.messages import get_messages
from django.utils.safestring import SafeString

from django.utils import timezone


class TimezoneMiddleware:
    COOKIE_NAME = "user_timezone"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timezone_name = request.COOKIES.get(self.COOKIE_NAME)

        if timezone_name:
            try:
                timezone.activate(ZoneInfo(timezone_name))
            except ZoneInfoNotFoundError:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)


class HtmxMessageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.headers.get("HX-Request") == "true":
            storage = get_messages(request)
            for message in storage:
                msg_data = {"message": str(message.message), "tags": message.tags}

                hx_trigger = response.get("HX-Trigger")
                if hx_trigger:
                    try:
                        trigger_data = json.loads(hx_trigger)
                        if isinstance(trigger_data, dict):
                            trigger_data["django-messages"] = msg_data
                            response["HX-Trigger"] = json.dumps(trigger_data)
                    except ValueError:
                        response["HX-Trigger"] = json.dumps(
                            {hx_trigger: {}, "django-messages": msg_data}
                        )
                else:
                    response["HX-Trigger"] = json.dumps({"django-messages": msg_data})

                break

        return response
