from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from .models import LedgerEntry, Payout, PayPalPayment, User


class PayPalError(Exception):
    pass


def enabled():
    return bool(
        settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET and settings.PAYPAL_MERCHANT_ID
    )


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def parse_money(amount):
    try:
        if amount["currency_code"] != "EUR" or not isinstance(amount["value"], str):
            raise PayPalError("Unexpected payment currency or value.")
        cents = Decimal(amount["value"]) * 100
        if not cents.is_finite() or cents != cents.to_integral_value() or not 0 < cents <= 1000000:
            raise PayPalError("Invalid payment amount.")
        return int(cents)
    except (KeyError, TypeError, InvalidOperation) as error:
        raise PayPalError("Invalid payment amount.") from error


class PayPalClient:
    def __init__(self):
        if not enabled():
            raise PayPalError("PayPal ist noch nicht konfiguriert.")

    def request(self, method, path, body=None, request_id=None):
        try:
            with httpx.Client(base_url=settings.PAYPAL_API_BASE, timeout=20) as client:
                token_response = client.post(
                    "/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                )
                token_response.raise_for_status()
                headers = {
                    "Authorization": f"Bearer {token_response.json()['access_token']}",
                    "Prefer": "return=representation",
                }
                if request_id:
                    headers["PayPal-Request-Id"] = request_id
                response = client.request(method, path, json=body, headers=headers)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise PayPalError(
                "PayPal konnte nicht bestaetigt werden. Bitte spaeter erneut pruefen."
            ) from error


def order_path(payment):
    if not payment.order_id:
        raise PayPalError("PayPal-Auftrag wurde noch nicht erstellt.")
    return f"/v2/checkout/orders/{quote(payment.order_id, safe='')}"


def create_payment(user, amount_cents, purpose, request_id):
    if purpose not in {"TOPUP", "DEBT"}:
        raise ValidationError("Unbekannter Zahlungszweck.")
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        payment = PayPalPayment.objects.filter(pk=request_id).first()
        if payment and payment.user_id != user.pk:
            raise ValidationError("Ungueltige Zahlungsanfrage.")
        if not payment:
            if purpose == "DEBT":
                amount_cents = max(0, -user.balance)
            if (
                not isinstance(amount_cents, int)
                or isinstance(amount_cents, bool)
                or not 0 < amount_cents <= 1000000
            ):
                raise ValidationError("Der Betrag muss zwischen 0,01 und 10.000,00 EUR liegen.")
            payment = PayPalPayment.objects.create(
                id=request_id, user=user, amount_cents=amount_cents, purpose=purpose
            )
        elif payment.purpose != purpose or (
            purpose == "TOPUP" and payment.amount_cents != amount_cents
        ):
            raise ValidationError("Diese Zahlungsanfrage wurde bereits anders verwendet.")
    if payment.order_id:
        return payment
    client = PayPalClient()
    data = client.request(
        "POST",
        "/v2/checkout/orders",
        {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": str(payment.pk),
                    "custom_id": str(payment.pk),
                    "invoice_id": str(payment.pk),
                    "payee": {"merchant_id": settings.PAYPAL_MERCHANT_ID},
                    "amount": {"currency_code": "EUR", "value": money(payment.amount_cents)},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "Weisswurstrunde",
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "return_url": f"{settings.PUBLIC_BASE_URL}/paypal/return/",
                        "cancel_url": f"{settings.PUBLIC_BASE_URL}/paypal/cancel/",
                    }
                }
            },
        },
        request_id=f"create-{payment.pk}",
    )
    approval = next(
        (
            link["href"]
            for link in data.get("links", [])
            if link.get("rel") in {"approve", "payer-action"}
        ),
        "",
    )
    parsed = urlparse(approval)
    expected_host = (
        "www.paypal.com" if settings.PAYPAL_ENVIRONMENT == "live" else "www.sandbox.paypal.com"
    )
    if not data.get("id") or parsed.scheme != "https" or parsed.hostname != expected_host:
        raise PayPalError("PayPal hat keine gueltige Freigabe-URL geliefert.")
    with transaction.atomic():
        payment = PayPalPayment.objects.select_for_update().get(pk=payment.pk)
        payment.order_id = data["id"]
        payment.approval_url = approval
        payment.save(update_fields=["order_id", "approval_url", "updated_at"])
    return payment


def create_payout(payout):
    if payout.method != Payout.Method.PAYPAL:
        raise PayPalError("Ungültige Auszahlungsmethode.")
    data = PayPalClient().request(
        "POST",
        "/v1/payments/payouts",
        {
            "sender_batch_header": {
                "sender_batch_id": f"payout-{payout.pk}",
                "email_subject": "Auszahlung aus der Weisswurstrunde",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {"value": money(payout.amount_cents), "currency": "EUR"},
                    "receiver": payout.recipient,
                    "note": payout.note or "Auszahlung aus der Weisswurstrunde",
                    "sender_item_id": str(payout.pk),
                }
            ],
        },
        request_id=f"payout-{payout.pk}",
    )
    batch_id = data.get("batch_header", {}).get("payout_batch_id")
    if not batch_id:
        raise PayPalError("PayPal hat keine Auszahlungs-ID geliefert.")
    return batch_id


def validate_order(payment, data):
    try:
        units = data["purchase_units"]
        if data["id"] != payment.order_id or data["intent"] != "CAPTURE" or len(units) != 1:
            raise PayPalError("Unexpected PayPal order.")
        unit = units[0]
        if (
            unit["custom_id"] != str(payment.pk)
            or unit["payee"]["merchant_id"] != settings.PAYPAL_MERCHANT_ID
            or parse_money(unit["amount"]) != payment.amount_cents
        ):
            raise PayPalError("PayPal order does not match the local payment.")
        return unit
    except (KeyError, TypeError, IndexError) as error:
        raise PayPalError("Incomplete PayPal order.") from error


@transaction.atomic
def sync_payment(payment_id, capture=False):
    initial = PayPalPayment.objects.get(pk=payment_id)
    user = User.objects.select_for_update().get(pk=initial.user_id)
    payment = PayPalPayment.objects.select_for_update().get(pk=payment_id)
    client = PayPalClient()
    data = client.request("GET", order_path(payment))
    unit = validate_order(payment, data)
    if (
        capture
        and payment.status != PayPalPayment.Status.CANCELLED
        and data.get("status") == "APPROVED"
        and not unit.get("payments", {}).get("captures")
    ):
        if payment.purpose == "DEBT" and payment.amount_cents > max(0, -user.balance):
            raise ValidationError(
                "Der offene Betrag hat sich verringert. Bitte eine neue Zahlung starten."
            )
        client.request(
            "POST", f"{order_path(payment)}/capture", {}, request_id=f"capture-{payment.pk}"
        )
        data = client.request("GET", order_path(payment))
        unit = validate_order(payment, data)
    captures = unit.get("payments", {}).get("captures", [])
    if len(captures) > 1:
        raise PayPalError("Unexpected multiple captures.")
    if captures:
        provider_capture = captures[0]
        if parse_money(provider_capture.get("amount")) != payment.amount_cents:
            raise PayPalError("Capture amount mismatch.")
        status = provider_capture.get("status")
        if status in {"COMPLETED", "PARTIALLY_REFUNDED", "REFUNDED"}:
            capture_id = provider_capture.get("id")
            if not capture_id or (payment.capture_id and payment.capture_id != capture_id):
                raise PayPalError("Capture ID mismatch.")
            payment.capture_id = capture_id
            entry, _ = LedgerEntry.objects.get_or_create(
                reference=f"paypal:capture:{capture_id}",
                defaults={
                    "user": user,
                    "amount_cents": payment.amount_cents,
                    "kind": LedgerEntry.Kind.PAYPAL,
                    "payment": payment,
                    "method": "PAYPAL",
                    "note": "PayPal Guthaben" if payment.purpose == "TOPUP" else "PayPal Ausgleich",
                },
            )
            if entry.payment_id != payment.pk or entry.amount_cents != payment.amount_cents:
                raise PayPalError("Capture has already been assigned to another payment.")
            payment.status = PayPalPayment.Status.COMPLETED
        elif not payment.capture_id:
            payment.status = (
                PayPalPayment.Status.FAILED
                if status in {"DECLINED", "FAILED"}
                else PayPalPayment.Status.PENDING
            )
    elif not payment.capture_id:
        provider_status = data.get("status")
        if provider_status == "VOIDED":
            payment.status = PayPalPayment.Status.CANCELLED
        elif provider_status == "APPROVED" and payment.status != PayPalPayment.Status.CANCELLED:
            payment.status = PayPalPayment.Status.APPROVED
        elif payment.status != PayPalPayment.Status.CANCELLED:
            payment.status = PayPalPayment.Status.CREATED
    for refund in unit.get("payments", {}).get("refunds", []):
        if refund.get("status") != "COMPLETED":
            continue
        if not payment.capture_id or not refund.get("id"):
            raise PayPalError("Refund without a verified capture.")
        amount = parse_money(refund.get("amount"))
        entry, _ = LedgerEntry.objects.get_or_create(
            reference=f"paypal:refund:{refund['id']}",
            defaults={
                "user": user,
                "amount_cents": -amount,
                "kind": LedgerEntry.Kind.REFUND,
                "payment": payment,
                "method": "PAYPAL",
                "note": "PayPal Erstattung",
            },
        )
        if entry.payment_id != payment.pk or entry.amount_cents != -amount:
            raise PayPalError("Refund does not match its original record.")
    payment.refunded_cents = -(
        payment.entries.filter(kind=LedgerEntry.Kind.REFUND).aggregate(total=Sum("amount_cents"))[
            "total"
        ]
        or 0
    )
    if payment.refunded_cents > payment.amount_cents:
        raise PayPalError("Refund exceeds captured amount.")
    if payment.refunded_cents:
        payment.status = (
            PayPalPayment.Status.REFUNDED
            if payment.refunded_cents == payment.amount_cents
            else PayPalPayment.Status.PARTIALLY_REFUNDED
        )
    payment.save()
    return payment


@transaction.atomic
def cancel_payment(payment_id):
    initial = PayPalPayment.objects.get(pk=payment_id)
    User.objects.select_for_update().get(pk=initial.user_id)
    payment = PayPalPayment.objects.select_for_update().get(pk=payment_id)
    if payment.order_id:
        payment = sync_payment(payment.pk)
    if payment.status not in {"CREATED", "APPROVED", "CANCELLED"} or payment.capture_id:
        raise ValidationError(
            "Diese Zahlung wurde bereits verarbeitet und kann nicht geschlossen werden."
        )
    payment.status = PayPalPayment.Status.CANCELLED
    payment.save(update_fields=["status", "updated_at"])
    return payment


def verify_webhook(headers, event):
    if not settings.PAYPAL_WEBHOOK_ID:
        raise PayPalError("Webhook is not configured.")
    names = {
        "auth_algo": "PAYPAL-AUTH-ALGO",
        "cert_url": "PAYPAL-CERT-URL",
        "transmission_id": "PAYPAL-TRANSMISSION-ID",
        "transmission_sig": "PAYPAL-TRANSMISSION-SIG",
        "transmission_time": "PAYPAL-TRANSMISSION-TIME",
    }
    if any(not headers.get(header) for header in names.values()):
        return False
    body = {field: headers[header] for field, header in names.items()}
    body.update(webhook_id=settings.PAYPAL_WEBHOOK_ID, webhook_event=event)
    result = PayPalClient().request("POST", "/v1/notifications/verify-webhook-signature", body)
    return result.get("verification_status") == "SUCCESS"


def process_webhook(event):
    event_type = event.get("event_type", "")
    if event_type not in {
        "CHECKOUT.ORDER.APPROVED",
        "CHECKOUT.ORDER.COMPLETED",
        "PAYMENT.CAPTURE.COMPLETED",
        "PAYMENT.CAPTURE.PENDING",
        "PAYMENT.CAPTURE.DENIED",
        "PAYMENT.CAPTURE.REFUNDED",
    }:
        return
    resource = event.get("resource", {})
    related = resource.get("supplementary_data", {}).get("related_ids", {})
    order_id = (
        resource.get("id") if event_type.startswith("CHECKOUT.ORDER.") else related.get("order_id")
    )
    payment = PayPalPayment.objects.filter(order_id=order_id).first() if order_id else None
    if payment is None:
        capture_id = related.get("capture_id") or resource.get("id")
        if event_type == "PAYMENT.CAPTURE.REFUNDED":
            capture_id = next(
                (
                    urlparse(link.get("href", "")).path.rstrip("/").split("/")[-1]
                    for link in resource.get("links", [])
                    if link.get("rel") == "up"
                ),
                capture_id,
            )
        payment = PayPalPayment.objects.filter(capture_id=capture_id).first()
    if payment is None:
        raise PayPalError("Payment not yet matched; retry notification.")
    sync_payment(payment.pk, capture=event_type == "CHECKOUT.ORDER.APPROVED")
