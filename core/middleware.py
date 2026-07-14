from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
