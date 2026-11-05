from datetime import datetime, time

from django.db import migrations
from django.db.models import Sum
from django.utils import timezone


def neutralize_prebooked_orders(apps, schema_editor):
    """Orders are charged at noon on the event day; reverse charges booked earlier."""
    from weisswurstrunde.audit import record

    Order = apps.get_model("weisswurstrunde", "Order")
    LedgerEntry = apps.get_model("weisswurstrunde", "LedgerEntry")
    now = timezone.now()
    orders = Order.objects.filter(event__date__gte=timezone.localdate(now)).select_related("event")
    for order in orders:
        event = order.event
        if now >= timezone.make_aware(datetime.combine(event.date, time(12))):
            continue
        booked = (
            LedgerEntry.objects.filter(order=order).aggregate(total=Sum("amount_cents"))["total"]
            or 0
        )
        if not booked:
            continue
        label = dict(EVENT_TYPES).get(event.event_type, event.event_type)
        LedgerEntry.objects.create(
            user_id=order.user_id,
            amount_cents=-booked,
            kind="ORDER",
            order=order,
            reference=f"order-prebooking-reversal:{order.pk}",
            note=f"{label} {event.date:%d.%m.%Y} Vorbuchung storniert",
            created_at=now,
        )
        record(
            "order_prebooking_reversed",
            order_id=order.pk,
            user_id=order.user_id,
            event_id=event.pk,
            amount_cents=-booked,
        )


EVENT_TYPES = [("WEISSWURST", "Wei\u00dfwurst"), ("LEBERKAESE", "Leberk\u00e4se")]


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0009_user_order_reminders_orderreminder"),
    ]

    operations = [
        migrations.RunPython(neutralize_prebooked_orders, migrations.RunPython.noop),
    ]
