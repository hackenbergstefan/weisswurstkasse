from django import template

register = template.Library()


@register.filter
def euros(value):
    cents = int(value or 0)
    sign = "-" if cents < 0 else ""
    euros_part, cents_part = divmod(abs(cents), 100)
    return (
        f"{sign}{euros_part:,},{cents_part:02d} \u20ac".replace(",", ".", 1)
        if euros_part >= 1000
        else f"{sign}{euros_part},{cents_part:02d} \u20ac"
    )


@register.filter
def absolute(value):
    return abs(int(value or 0))


@register.filter
def initials(value):
    return "".join(part[0] for part in str(value).split()[:2]).upper()
