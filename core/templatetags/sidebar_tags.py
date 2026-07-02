from django import template
from django.urls import reverse, NoReverseMatch

register = template.Library()

@register.simple_tag(takes_context=True)
def active_link(context, view_name, base_classes, active_classes):
    request = context.get('request')
    if not request:
        return base_classes
    
    try:
        target_url = reverse(view_name)
    except NoReverseMatch:
        target_url = view_name

    if request.path == target_url:
        return active_classes

    elif request.path.startswith(target_url) and target_url != '/':
        return active_classes
    
    return base_classes 