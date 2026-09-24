import uuid
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import DefaultItem, Event, EventType, LedgerEntry, Order, OrderItem, Product, User


def validate_quantities(quantities):
    if not isinstance(quantities, dict) or any(
        not isinstance(product_id, int)
        or isinstance(product_id, bool)
        or not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or not 0 <= quantity <= 100
        for product_id, quantity in quantities.items()
    ):
        raise ValidationError("Mengen muessen ganze Zahlen zwischen 0 und 100 sein.")


@transaction.atomic
def save_order(order_id, quantities, actor, expected_version=None):
    validate_quantities(quantities)
    initial = Order.objects.get(pk=order_id)
    User.objects.select_for_update().get(pk=initial.user_id)
    order = Order.objects.select_for_update().select_related("event").get(pk=order_id)
    if not order.event.can_edit(actor):
        raise ValidationError("Der Bestellschluss ist vorbei. Die Bestellung bleibt unveraendert.")
    if expected_version is not None and order.version != expected_version:
        raise ValidationError("Die Bestellung wurde inzwischen geaendert. Bitte neu laden.")
    old_items = {item.product_id: item for item in order.items.all()}
    products = {
        product.pk: product
        for product in Product.objects.filter(pk__in=quantities, event_type=order.event.event_type)
    }
    if set(products) != set(quantities):
        raise ValidationError("Unbekanntes Produkt oder Produkt eines anderen Veranstaltungstyps.")
    if any(
        not product.active
        and quantities[product_id] != getattr(old_items.get(product_id), "quantity", 0)
        for product_id, product in products.items()
    ):
        raise ValidationError("Inaktive Produkte koennen nicht geaendert werden.")
    old_total = order.total
    for product_id, quantity in quantities.items():
        product = products[product_id]
        if not product.active:
            continue
        if quantity:
            OrderItem.objects.update_or_create(
                order=order,
                product=product,
                defaults={"quantity": quantity, "unit_price_cents": product.price_cents},
            )
        else:
            order.items.filter(product=product).delete()
    order.version += 1
    order.save(update_fields=["version", "updated_at"])
    delta = old_total - order.total
    if delta:
        LedgerEntry.objects.create(
            user=order.user,
            amount_cents=delta,
            kind=LedgerEntry.Kind.ORDER,
            order=order,
            recorded_by=actor,
            reference=f"order:{order.pk}:{order.version}",
            note=f"{order.event.get_event_type_display()} {order.event.date:%d.%m.%Y} (Stand {order.version})",
        )
    return order


@transaction.atomic
def save_defaults(user, quantities):
    validate_quantities(quantities)
    User.objects.select_for_update().get(pk=user.pk)
    products = Product.objects.filter(pk__in=quantities, active=True)
    if products.count() != len(quantities):
        raise ValidationError("Unbekanntes oder inaktives Produkt.")
    user.default_items.all().delete()
    DefaultItem.objects.bulk_create(
        [
            DefaultItem(user=user, product=product, quantity=quantities[product.pk])
            for product in products
            if quantities[product.pk]
        ]
    )


@transaction.atomic
def add_order(user, event, actor):
    if not actor.is_active or not actor.is_admin:
        raise PermissionDenied
    participant = User.objects.select_for_update().get(pk=user.pk)
    if not participant.is_active:
        raise ValidationError("Der Teilnehmer ist nicht aktiv.")
    order, _ = Order.objects.get_or_create(user=participant, event=event)
    return order


def provision_order(user, event):
    order, created = Order.objects.get_or_create(user=user, event=event)
    if created and event.editable:
        quantities = dict(
            user.default_items.filter(
                product__active=True, product__event_type=event.event_type
            ).values_list("product_id", "quantity")
        )
        save_order(order.pk, quantities, user)
    return order


@transaction.atomic
def generate_events(today=None):
    today = today or timezone.localdate()
    active_users = list(User.objects.filter(is_active=True))
    created_count = 0
    schedules = (
        (EventType.WEISSWURST, settings.WEISSWURST_WEEKDAY),
        (EventType.LEBERKAESE, settings.LEBERKAESE_WEEKDAY),
    )
    for event_type, weekday in schedules:
        first = today + timedelta(days=(weekday - today.weekday()) % 7)
        for offset in range(settings.UPCOMING_WEEKS + 1):
            event_date = first + timedelta(weeks=offset)
            deadline_date = event_date - timedelta(days=settings.DEADLINE_DAYS_BEFORE)
            deadline = timezone.make_aware(
                datetime.combine(deadline_date, time.fromisoformat(settings.DEADLINE_TIME))
            )
            event, created = Event.objects.get_or_create(
                date=event_date, event_type=event_type, defaults={"deadline": deadline}
            )
            if created:
                created_count += 1
            if event.editable:
                for user in active_users:
                    provision_order(user, event)
    Event.objects.filter(status=Event.Status.OPEN, deadline__lte=timezone.now()).update(
        status=Event.Status.LOCKED
    )
    Event.objects.filter(date__lt=today).exclude(status=Event.Status.SETTLED).update(
        status=Event.Status.SETTLED
    )
    return created_count


@transaction.atomic
def manual_payment(user, amount_cents, method, note, request_id, actor=None):
    if (
        not isinstance(amount_cents, int)
        or isinstance(amount_cents, bool)
        or not 0 < amount_cents <= 1000000
    ):
        raise ValidationError("Der Betrag muss zwischen 0,01 und 10.000,00 EUR liegen.")
    if method not in {"CASH", "BANK", "OTHER"}:
        raise ValidationError("Unbekannte Zahlungsart.")
    User.objects.select_for_update().get(pk=user.pk)
    reference = f"manual:{user.pk}:{uuid.UUID(str(request_id))}"
    existing = LedgerEntry.objects.filter(reference=reference).first()
    if existing:
        if (existing.amount_cents, existing.method) != (amount_cents, method):
            raise ValidationError("Diese Zahlungsanfrage wurde bereits anders verbucht.")
        return existing
    return LedgerEntry.objects.create(
        user=user,
        amount_cents=amount_cents,
        kind=LedgerEntry.Kind.MANUAL,
        method=method,
        note=note,
        reference=reference,
        recorded_by=actor or user,
    )


@transaction.atomic
def reverse_manual(entry_id, actor, note):
    entry = LedgerEntry.objects.get(pk=entry_id)
    User.objects.select_for_update().get(pk=entry.user_id)
    if entry.kind != LedgerEntry.Kind.MANUAL or not note.strip():
        raise ValidationError("Nur manuelle Zahlungen koennen mit Begruendung korrigiert werden.")
    reversal, _ = LedgerEntry.objects.get_or_create(
        reference=f"correction:{entry.pk}",
        defaults={
            "user": entry.user,
            "amount_cents": -entry.amount_cents,
            "kind": LedgerEntry.Kind.CORRECTION,
            "reverses": entry,
            "recorded_by": actor,
            "note": note,
            "method": entry.method,
        },
    )
    return reversal


def reconcile():
    issues = []
    for order in Order.objects.prefetch_related("items"):
        booked = order.charges.aggregate(total=Sum("amount_cents"))["total"] or 0
        if booked != -order.total:
            issues.append(f"Order {order.pk}: ledger {booked}, expected {-order.total}")
    for user in User.objects.all():
        for payment in user.paypal_payments.all():
            expected = payment.amount_cents - payment.refunded_cents if payment.capture_id else 0
            actual = payment.entries.aggregate(total=Sum("amount_cents"))["total"] or 0
            if expected != actual:
                issues.append(f"PayPal {payment.pk}: ledger {actual}, expected {expected}")
    return issues
