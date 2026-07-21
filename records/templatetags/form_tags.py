from django import template
from django.urls import reverse

register = template.Library()


@register.filter
def get_attr(obj, attr):
    return getattr(obj, attr, "")


@register.simple_tag(takes_context=True)
def filter_url(context, view_name, **kwargs):
    request = context.get("request")
    if not request:
        return reverse(view_name)

    query_params = request.GET.copy()

    for key, value in kwargs.items():
        if value is None:
            query_params.pop(key, None)
        else:
            query_params[key] = value

    base_url = reverse(view_name)

    if query_params:
        return f"{base_url}?{query_params.urlencode()}"
    return base_url
