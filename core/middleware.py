import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.messages import get_messages
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

        if (
            request.headers.get("HX-Request") == "true"
            and "HX-Redirect" not in response
            and "HX-Refresh" not in response
        ):
            storage = get_messages(request)
            messages_list = []

            for message in storage:
                messages_list.append({"message": str(message.message), "level": message.level})

            if messages_list:
                hx_trigger = response.get("HX-Trigger")

                payload = {"djangoMessages": messages_list}

                if hx_trigger:
                    try:
                        trigger_data = json.loads(hx_trigger)
                        if isinstance(trigger_data, dict):
                            trigger_data.update(payload)
                            response["HX-Trigger"] = json.dumps(trigger_data)
                    except ValueError:
                        response["HX-Trigger"] = json.dumps(
                            {hx_trigger: {}, "djangoMessages": messages_list}
                        )
                else:
                    response["HX-Trigger"] = json.dumps(payload)

        return response
