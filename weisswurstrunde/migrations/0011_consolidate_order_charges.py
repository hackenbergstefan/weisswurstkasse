from datetime import datetime, time

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

EVENT_TYPES = {"WEISSWURST": "Wei\u00dfwurst", "LEBERKAESE": "Leberk\u00e4se"}


def consolidate_order_charges(apps, schema_editor):
    """One-time rewrite: replace order entries booked before noon on the event day.

    Past orders get a single charge at 12:00 on the event day with the same net amount;
    future orders lose their pre-bookings because the worker charges them at noon.
    Later corrections stay untouched, so every balance remains unchanged.
    """
    from weisswurstrunde.audit import record

    Order = apps.get_model("weisswurstrunde", "Order")
    LedgerEntry = apps.get_model("weisswurstrunde", "LedgerEntry")
    now = timezone.now()
    for order in Order.objects.select_related("event"):
        event = order.event
        charge_at = timezone.make_aware(datetime.combine(event.date, time(12)))
        entries = list(
            LedgerEntry._base_manager.filter(order=order, reverses__isnull=True)
            .filter(
                Q(created_at__lt=charge_at)
                | Q(reference__startswith="order-prebooking-reversal:")
                | Q(reference=f"order-charge:{order.pk}")
            )
            .exclude(reversal__isnull=False)
            .order_by("created_at", "pk")
        )
        if not entries or [entry.reference for entry in entries] == [f"order-charge:{order.pk}"]:
            continue
        amount_cents = sum(entry.amount_cents for entry in entries)
        LedgerEntry._base_manager.filter(pk__in=[entry.pk for entry in entries]).delete()
        charge_reference = None
        if now >= charge_at and amount_cents:
            charge_reference = f"order-charge:{order.pk}"
            LedgerEntry._base_manager.create(
                user_id=order.user_id,
                amount_cents=amount_cents,
                kind="ORDER",
                order=order,
                reference=charge_reference,
                note=f"{EVENT_TYPES.get(event.event_type, event.event_type)} {event.date:%d.%m.%Y}",
                created_at=charge_at,
            )
        record(
            "ledger_order_entries_consolidated",
            order_id=order.pk,
            user_id=order.user_id,
            event_id=event.pk,
            charge_reference=charge_reference,
            amount_cents=amount_cents if charge_reference else 0,
            removed_entries=[
                {
                    "id": entry.pk,
                    "amount_cents": entry.amount_cents,
                    "reference": entry.reference,
                    "created_at": entry.created_at.isoformat(),
                    "recorded_by_id": entry.recorded_by_id,
                }
                for entry in entries
            ],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0010_neutralize_prebooked_orders"),
    ]

    operations = [
        migrations.RunPython(consolidate_order_charges, migrations.RunPython.noop),
    ]
