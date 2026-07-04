from django import template
from django.urls import reverse
from urllib.parse import urlencode

register = template.Library()

@register.simple_tag
def filter_url(view_name, **kwargs):
    base_url = reverse(view_name)
    clean_filters = {k: v for k, v in kwargs.items() if v is not None}
    
    if clean_filters:
        return f"{base_url}?{urlencode(clean_filters)}"
    else:
        return f"{base_url}"
