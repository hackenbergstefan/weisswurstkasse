import uuid
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q, Sum
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils import timezone

from .audit import record
from .models import (
    DefaultItem,
    Event,
    EventType,
    LedgerEntry,
    Order,
    OrderItem,
    Payout,
    Product,
    User,
)


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
    closed_order = not order.event.editable
    if not order.event.can_edit(actor):
        raise ValidationError("Der Bestellschluss ist vorbei. Die Bestellung bleibt unveraendert.")
    if expected_version is not None and order.version != expected_version:
        raise ValidationError("Die Bestellung wurde inzwischen geaendert. Bitte neu laden.")
    old_items = {item.product_id: item for item in order.items.all()}
    old_snapshot = {
        str(product_id): {
            "quantity": item.quantity,
            "unit_price_cents": item.unit_price_cents,
        }
        for product_id, item in old_items.items()
    }
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
    new_snapshot = {
        str(item.product_id): {
            "quantity": item.quantity,
            "unit_price_cents": item.unit_price_cents,
        }
        for item in order.items.all()
    }
    product_names = dict(
        Product.objects.filter(
            pk__in={int(product_id) for product_id in old_snapshot | new_snapshot}
        ).values_list("pk", "name")
    )
    order_delta = [
        {
            "name": product_names[int(product_id)],
            "old_quantity": old_snapshot.get(product_id, {}).get("quantity", 0),
            "new_quantity": new_snapshot.get(product_id, {}).get("quantity", 0),
            "quantity_delta": new_snapshot.get(product_id, {}).get("quantity", 0)
            - old_snapshot.get(product_id, {}).get("quantity", 0),
        }
        for product_id in sorted(old_snapshot | new_snapshot, key=int)
        if old_snapshot.get(product_id, {}).get("quantity", 0)
        != new_snapshot.get(product_id, {}).get("quantity", 0)
    ]
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
    record(
        "order_edited",
        actor,
        order_id=order.pk,
        user_id=order.user_id,
        event_id=order.event_id,
        version=order.version,
        old_items=old_snapshot,
        new_items={product_id: item for product_id, item in new_snapshot.items()},
        total_cents=order.total,
    )
    if closed_order and old_snapshot != new_snapshot:
        transaction.on_commit(
            lambda order_id=order.pk, old_total=old_total, order_delta=order_delta: (
                send_order_changed_email(order_id, old_total, order_delta)
            )
        )
    return order


@transaction.atomic
def save_defaults(user, quantities):
    validate_quantities(quantities)
    User.objects.select_for_update().get(pk=user.pk)
    products = Product.objects.filter(pk__in=quantities, active=True)
    if products.count() != len(quantities):
        raise ValidationError("Unbekanntes oder inaktives Produkt.")
    previous_defaults = dict(user.default_items.values_list("product_id", "quantity"))
    previous_product_types = dict(
        Product.objects.filter(pk__in=previous_defaults).values_list("pk", "event_type")
    )
    upcoming_orders = (
        user.orders.filter(event__date__gte=timezone.localdate())
        .filter(event__status=Event.Status.OPEN, event__deadline__gt=timezone.now())
        .select_related("event")
        .prefetch_related("items")
    )
    for order in upcoming_orders:
        old_quantities = {
            product_id: quantity
            for product_id, quantity in previous_defaults.items()
            if previous_product_types.get(product_id) == order.event.event_type
        }
        current_quantities = {
            item.product_id: item.quantity for item in order.items.all() if item.quantity
        }
        if current_quantities != old_quantities:
            continue
        new_quantities = {
            product.pk: quantities[product.pk]
            for product in products
            if product.event_type == order.event.event_type
        }
        save_order(order.pk, new_quantities, user)
    user.default_items.all().delete()
    DefaultItem.objects.bulk_create(
        [
            DefaultItem(user=user, product=product, quantity=quantities[product.pk])
            for product in products
            if quantities[product.pk]
        ]
    )
    record("defaults_edited", user, user_id=user.pk, quantities=quantities)


@transaction.atomic
def add_order(user, event, actor):
    if not actor.is_active or not actor.is_admin:
        raise PermissionDenied
    if event.status == Event.Status.CANCELLED:
        raise ValidationError("Abgesagte Termine koennen keine Bestellungen enthalten.")
    participant = User.objects.select_for_update().get(pk=user.pk)
    if not participant.is_active:
        raise ValidationError("Der Teilnehmer ist nicht aktiv.")
    order, _ = Order.objects.get_or_create(user=participant, event=event)
    record("order_created", actor, order_id=order.pk, user_id=user.pk, event_id=event.pk)
    return order


@transaction.atomic
def cancel_event(event_id, actor):
    if not actor.is_active or not actor.is_admin:
        raise PermissionDenied
    event = Event.objects.select_for_update().get(pk=event_id)
    if event.status == Event.Status.SETTLED:
        raise ValidationError("Abgeschlossene Termine koennen nicht abgesagt werden.")
    if event.status == Event.Status.CANCELLED:
        return event

    user_ids = Order.objects.filter(event=event).values_list("user_id", flat=True)
    list(User.objects.select_for_update().filter(pk__in=user_ids))
    orders = list(Order.objects.select_for_update().filter(event=event).prefetch_related("items"))
    for order in orders:
        total = order.total
        if order.items.exists():
            order.items.all().delete()
            order.version += 1
            order.save(update_fields=["version", "updated_at"])
        if total:
            LedgerEntry.objects.create(
                user=order.user,
                amount_cents=total,
                kind=LedgerEntry.Kind.ORDER,
                order=order,
                recorded_by=actor,
                reference=f"event-cancel:{event.pk}:order:{order.pk}",
                note=f"{event.get_event_type_display()} {event.date:%d.%m.%Y} abgesagt",
            )
    event.status = Event.Status.CANCELLED
    event.save(update_fields=["status"])
    record("event_cancelled", actor, event_id=event.pk, order_count=len(orders))
    return event


@transaction.atomic
def create_payout(amount_cents, method, recipient, note, actor):
    if not actor.is_active or not actor.is_admin:
        raise PermissionDenied
    if method not in Payout.Method.values:
        raise ValidationError("Unbekannte Auszahlungsmethode.")
    payout = Payout.objects.create(
        amount_cents=amount_cents,
        method=method,
        recipient=recipient,
        note=note,
        created_by=actor,
        status=Payout.Status.COMPLETED if method == Payout.Method.CASH else Payout.Status.PENDING,
    )
    payout.save(update_fields=["status", "provider_batch_id"])
    record(
        "payout_created",
        actor,
        payout_id=payout.pk,
        amount_cents=amount_cents,
        method=method,
        status=payout.status,
    )
    return payout


def provision_order(user, event):
    order, created = Order.objects.get_or_create(user=user, event=event)
    if created and event.editable and not user.is_on_vacation(event.date):
        quantities = dict(
            user.default_items.filter(
                product__active=True, product__event_type=event.event_type
            ).values_list("product_id", "quantity")
        )
        save_order(order.pk, quantities, user)
    return order


def event_balance(user, event):
    return (
        user.ledger.filter(
            Q(status="POSTED"),
            Q(created_at__date__lte=event.date),
            Q(order__isnull=True) | Q(order__event__date__lte=event.date),
        ).aggregate(total=Sum("amount_cents"))["total"]
        or 0
    )


def send_order_close_emails(event_id):
    event = Event.objects.get(pk=event_id)
    logo_url = f"{settings.PUBLIC_BASE_URL}{static('weisswurst-logo.png')}"
    orders = list(
        Order.objects.filter(event=event)
        .select_related("user")
        .prefetch_related("items__product")
        .order_by("user__name")
    )
    for order in orders:
        context = {
            "event": event,
            "order": order,
            "balance": event_balance(order.user, event),
            "logo_url": logo_url,
        }
        message = EmailMultiAlternatives(
            subject=f"Deine Bestellung: {event.get_event_type_display()} am {event.date:%d.%m.%Y}",
            body=render_to_string("weisswurstrunde/email/order_closed.txt", context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[order.user.email],
        )
        message.attach_alternative(
            render_to_string("weisswurstrunde/email/order_closed.html", context), "text/html"
        )
        message.send()

    admin_emails = list(
        User.objects.filter(is_active=True, is_admin=True).values_list("email", flat=True)
    )
    if admin_emails:
        record(
            "deadline_order_list_sent",
            event_id=event.pk,
            event_type=event.event_type,
            event_date=event.date,
            orders=[
                {
                    "order_id": order.pk,
                    "user_id": order.user_id,
                    "user_name": order.user.name,
                    "user_email": order.user.email,
                    "items": [
                        {
                            "product_id": item.product_id,
                            "product": item.product.name,
                            "quantity": item.quantity,
                            "unit_price_cents": item.unit_price_cents,
                        }
                        for item in order.items.all()
                    ],
                    "total_cents": order.total,
                }
                for order in orders
            ],
        )
        product_totals = {}
        for order in orders:
            for item in order.items.all():
                total = product_totals.setdefault(
                    item.product_id, {"name": item.product.name, "quantity": 0}
                )
                total["quantity"] += item.quantity
        orders_with_items = [order for order in orders if order.items.all()]
        context = {
            "event": event,
            "orders": orders_with_items,
            "participant_count": len(orders_with_items),
            "product_totals": product_totals.values(),
            "logo_url": logo_url,
        }
        message = EmailMultiAlternatives(
            subject=f"Gesamtbestellung: {event.get_event_type_display()} am {event.date:%d.%m.%Y}",
            body=render_to_string("weisswurstrunde/email/orders_closed.txt", context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=admin_emails,
        )
        message.attach_alternative(
            render_to_string("weisswurstrunde/email/orders_closed.html", context), "text/html"
        )
        message.send()


def send_order_changed_email(order_id, old_total, order_delta):
    order = (
        Order.objects.select_related("event", "user")
        .prefetch_related("items__product")
        .get(pk=order_id)
    )
    admin_emails = list(
        User.objects.filter(is_active=True, is_admin=True).values_list("email", flat=True)
    )
    if not admin_emails:
        return
    event = order.event
    orders = list(
        Order.objects.filter(event=event)
        .select_related("user")
        .prefetch_related("items__product")
        .order_by("user__name")
    )
    product_totals = {}
    for current_order in orders:
        for item in current_order.items.all():
            total = product_totals.setdefault(
                item.product_id, {"name": item.product.name, "quantity": 0}
            )
            total["quantity"] += item.quantity
    orders_with_items = [current_order for current_order in orders if current_order.items.all()]
    context = {
        "event": event,
        "orders": orders_with_items,
        "participant_count": len(orders_with_items),
        "product_totals": product_totals.values(),
        "logo_url": f"{settings.PUBLIC_BASE_URL}{static('weisswurst-logo.png')}",
        "change": {
            "order": order,
            "old_total": old_total,
            "new_total": order.total,
            "delta": order_delta,
        },
    }
    message = EmailMultiAlternatives(
        subject=f"Geaenderte Gesamtbestellung: {event.get_event_type_display()} am {event.date:%d.%m.%Y}",
        body=render_to_string("weisswurstrunde/email/orders_closed.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=admin_emails,
    )
    message.attach_alternative(
        render_to_string("weisswurstrunde/email/orders_closed.html", context), "text/html"
    )
    message.send()


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
                record(
                    "event_created",
                    event_id=event.pk,
                    event_type=event.event_type,
                    event_date=event.date,
                )
            if event.editable:
                for user in active_users:
                    provision_order(user, event)
    closing_event_ids = list(
        Event.objects.filter(status=Event.Status.OPEN, deadline__lte=timezone.now()).values_list(
            "pk", flat=True
        )
    )
    Event.objects.filter(pk__in=closing_event_ids).update(status=Event.Status.LOCKED)
    for event_id in closing_event_ids:
        record("event_locked", event_id=event_id)
        transaction.on_commit(lambda event_id=event_id: send_order_close_emails(event_id))
    settling_event_ids = list(
        Event.objects.filter(date__lt=today)
        .exclude(status=Event.Status.SETTLED)
        .values_list("pk", flat=True)
    )
    Event.objects.filter(pk__in=settling_event_ids).update(status=Event.Status.SETTLED)
    for event_id in settling_event_ids:
        record("event_settled", event_id=event_id)
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
    entry = LedgerEntry.objects.create(
        user=user,
        amount_cents=amount_cents,
        kind=LedgerEntry.Kind.MANUAL,
        method=method,
        note=note,
        reference=reference,
        recorded_by=actor or user,
    )
    record(
        "manual_payment_recorded",
        actor or user,
        entry_id=entry.pk,
        user_id=user.pk,
        amount_cents=amount_cents,
        method=method,
    )
    return entry


@transaction.atomic
def reverse_manual(entry_id, actor, note):
    entry = LedgerEntry.objects.get(pk=entry_id)
    User.objects.select_for_update().get(pk=entry.user_id)
    if entry.kind != LedgerEntry.Kind.MANUAL or not note.strip():
        raise ValidationError("Nur manuelle Zahlungen koennen mit Begruendung korrigiert werden.")
    reversal, created = LedgerEntry.objects.get_or_create(
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
    if created:
        record(
            "manual_payment_reversed",
            actor,
            entry_id=entry.pk,
            reversal_id=reversal.pk,
            amount_cents=reversal.amount_cents,
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
            expected = (
                payment.amount_cents - payment.refunded_cents
                if payment.status
                in {
                    "COMPLETED",
                    "PARTIALLY_REFUNDED",
                    "REFUNDED",
                }
                else 0
            )
            actual = payment.entries.aggregate(total=Sum("amount_cents"))["total"] or 0
            if expected != actual:
                issues.append(f"PayPal {payment.pk}: ledger {actual}, expected {expected}")
    return issues
