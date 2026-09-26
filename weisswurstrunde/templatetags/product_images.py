from django import template
from django.conf import settings
from django.templatetags.static import static

register = template.Library()


def product_image_filename(name):
    normalized_name = str(name).lower()
    if "leber" in normalized_name:
        return "leberkas-neu.png"
    if "wurst" in normalized_name:
        return "weisswurst-neu.png"
    if "brez" in normalized_name:
        return "breze-neu.png"
    return ""


@register.simple_tag
def product_image(name):
    filename = product_image_filename(name)
    return static(filename) if filename else ""


@register.simple_tag
def product_image_url(name):
    filename = product_image_filename(name)
    return f"{settings.PUBLIC_BASE_URL}{static(filename)}" if filename else ""
