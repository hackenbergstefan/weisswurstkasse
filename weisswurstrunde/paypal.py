import email
import hashlib
import html
import imaplib
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import LedgerEntry, Payout, PayPalPayment, User

logger = logging.getLogger(__name__)
OPEN_PAYMENT_STATUSES = (
    PayPalPayment.Status.CREATED,
    PayPalPayment.Status.APPROVED,
    PayPalPayment.Status.PENDING,
)


class PayPalError(Exception):
    pass


def enabled():
    return bool(settings.PAYPAL_ME_LINK)


def mailbox_enabled():
    return bool(
        settings.PAYPAL_IMAP_HOST and settings.PAYPAL_IMAP_USER and settings.PAYPAL_IMAP_PASSWORD
    )


def money(cents):
    return f"{cents // 100},{cents % 100:02d}"


def _paypal_me_url(amount_cents):
    amount = str(amount_cents // 100) if amount_cents % 100 == 0 else money(amount_cents)
    return f"{settings.PAYPAL_ME_LINK.rstrip('/')}/{amount}"


def _matching_cents():
    open_cents = set(
        PayPalPayment.objects.select_for_update()
        .filter(
            status__in=OPEN_PAYMENT_STATUSES,
            matching_cents__isnull=False,
        )
        .values_list("matching_cents", flat=True)
    )
    available_cents = list(set(range(31)) - open_cents)
    if not available_cents:
        raise ValidationError("Es sind bereits zu viele PayPal-Zahlungen offen.")
    return secrets.choice(available_cents)


def create_payment(user, amount_cents, purpose, request_id):
    if not enabled():
        raise PayPalError("PayPal.me ist noch nicht konfiguriert.")
    if purpose not in {"TOPUP", "DEBT"}:
        raise ValidationError("Unbekannter Zahlungszweck.")
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        payment = PayPalPayment.objects.filter(pk=request_id).first()
        if payment and payment.user_id != user.pk:
            raise ValidationError("Ungueltige Zahlungsanfrage.")
        if not payment:
            if PayPalPayment.objects.filter(user=user, status__in=OPEN_PAYMENT_STATUSES).exists():
                raise ValidationError("Du hast bereits eine offene PayPal-Einzahlung.")
            if purpose == "DEBT":
                amount_cents = max(0, -user.balance)
            if (
                not isinstance(amount_cents, int)
                or isinstance(amount_cents, bool)
                or not 0 < amount_cents <= 1000000
            ):
                raise ValidationError("Der Betrag muss zwischen 0,01 und 10.000,00 EUR liegen.")
            matching_cents = _matching_cents()
            payment = PayPalPayment.objects.create(
                id=request_id,
                user=user,
                amount_cents=amount_cents + matching_cents,
                matching_cents=matching_cents,
                purpose=purpose,
                status=PayPalPayment.Status.PENDING,
                approval_url=_paypal_me_url(amount_cents + matching_cents),
            )
        elif payment.purpose != purpose or (
            purpose == "TOPUP"
            and payment.amount_cents - (payment.matching_cents or 0) != amount_cents
        ):
            raise ValidationError("Diese Zahlungsanfrage wurde bereits anders verwendet.")
    return payment


def _decode(value):
    return str(make_header(decode_header(value or "")))


def _body(message):
    parts = message.walk() if message.is_multipart() else [message]
    values = []
    for part in parts:
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            values.append(part.get_content())
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            values.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(values)


def _plain_text(text):
    text = re.sub(
        r"<(?:style|script)\b[^>]*>.*?</(?:style|script)>",
        " ",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _amount(text):
    matches = re.findall(r"(?:EUR\s*)?(\d{1,6}(?:[.,]\d{2}))\s*(?:EUR|€)", text, re.IGNORECASE)
    if not matches:
        matches = re.findall(r"€\s*(\d{1,6}(?:[.,]\d{2}))", text)
    if not matches:
        return None
    try:
        raw = matches[0]
        value = Decimal(raw.replace(",", "."))
        cents = value * 100
        return int(cents) if cents == cents.to_integral_value() else None
    except (InvalidOperation, ValueError):
        return None


def _reference(text):
    match = re.search(
        r"(?:transaction\s*id|transaktions(?:code|nummer)|transaktions-id)\s*[:#]?\s*([A-Z0-9-]{8,})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def _recipient(text):
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    recipient_email = email_match.group(0) if email_match else ""
    name_match = re.search(
        r"(?:payment\s+to|zahlung\s+an|(?:\d[\d.,]*\s*(?:€\s*)?EUR)\s+an)\s+(.+?)(?=\s+(?:for|in the amount|sent|gesendet)|[.(])",
        text,
        re.IGNORECASE,
    )
    recipient_name = name_match.group(1).strip(" :,-") if name_match else ""
    return recipient_name, recipient_email


def parse_message(raw, message_id):
    message = email.message_from_bytes(raw, policy=policy.default)
    subject = _decode(message.get("Subject"))
    text = _plain_text(f"{subject}\n{_body(message)}")
    outgoing = bool(
        re.search(r"you sent|sie haben eine zahlung gesendet|zahlung gesendet", text, re.IGNORECASE)
    )
    incoming = bool(
        re.search(
            r"you received|sie haben eine zahlung erhalten|zahlung erhalten", text, re.IGNORECASE
        )
    )
    if not (incoming or outgoing):
        return None
    recipient_name, recipient_email = _recipient(text) if outgoing else ("", "")
    try:
        received_at = parsedate_to_datetime(message.get("Date"))
    except (TypeError, ValueError, IndexError, OverflowError):
        received_at = timezone.now()
    if received_at.tzinfo is None:
        received_at = timezone.make_aware(received_at)
    return {
        "message_id": message.get("Message-ID") or str(message_id),
        "amount_cents": _amount(text),
        "reference": _reference(text),
        "recipient_name": recipient_name,
        "recipient_email": recipient_email,
        "text": text.lower(),
        "outgoing": outgoing,
        "received_at": received_at,
    }


def _match_payment(data):
    payments = PayPalPayment.objects.select_for_update().filter(
        status__in=[PayPalPayment.Status.CREATED, PayPalPayment.Status.PENDING]
    )
    candidates = [payment for payment in payments if payment.amount_cents == data["amount_cents"]]
    return candidates[0] if len(candidates) == 1 else None


def _incoming_folder(user):
    return f"Einzahlungen/{user.pk}-{slugify(user.name) or 'user'}"


def _target_folder(data):
    payment = (
        PayPalPayment.objects.select_related("user")
        .filter(provider_message_id=data["message_id"])
        .first()
    )
    if payment:
        return _incoming_folder(payment.user)
    if Payout.objects.filter(provider_message_id=data["message_id"]).exists():
        return "Auszahlungen"
    return None


def _move_message(mailbox, message_id, target):
    delimiter = "."
    imap_target = delimiter.join(target.split("/"))
    folders = imap_target.split(delimiter)
    for index in range(1, len(folders) + 1):
        folder = delimiter.join(folders[:index])
        result = mailbox.create(folder)
        status = result[0] if isinstance(result, tuple) else "OK"
        logger.debug("PayPal-IMAP: Ordner %s anlegen: %s", folder, status)
    try:
        status, _ = mailbox.move(message_id, imap_target)
    except (AttributeError, imaplib.IMAP4.error):
        status = "NO"
    if status == "OK":
        logger.debug("PayPal-IMAP: Nachricht %s nach %s verschoben (MOVE)", message_id, imap_target)
        return
    status, _ = mailbox.copy(message_id, imap_target)
    if status != "OK":
        raise PayPalError(f"PayPal-E-Mail konnte nicht nach {target} verschoben werden.")
    status, _ = mailbox.store(message_id, "+FLAGS", "\\Deleted")
    if status != "OK":
        raise PayPalError(f"PayPal-E-Mail konnte nicht nach {target} verschoben werden.")
    logger.debug("PayPal-IMAP: Nachricht %s nach %s verschoben (COPY)", message_id, imap_target)


@transaction.atomic
def _apply_message(data, payment_id=None):
    if not data or not data["amount_cents"]:
        return False
    if (
        PayPalPayment.objects.filter(provider_message_id=data["message_id"]).exists()
        or Payout.objects.filter(provider_message_id=data["message_id"]).exists()
        or (
            data["reference"]
            and PayPalPayment.objects.filter(provider_reference=data["reference"]).exists()
        )
    ):
        return False
    if data["outgoing"]:
        provider_reference = (
            data["reference"] or hashlib.sha256(data["message_id"].encode()).hexdigest()
        )
        actor = User.objects.filter(is_admin=True, is_active=True).order_by("pk").first()
        note_parts = []
        if data["recipient_name"]:
            note_parts.append(f"Empfänger: {data['recipient_name']}")
        if data["reference"]:
            note_parts.append(f"Transaktionscode: {data['reference']}")
        Payout.objects.create(
            amount_cents=data["amount_cents"],
            method=Payout.Method.PAYPAL,
            status=Payout.Status.COMPLETED,
            recipient=data["recipient_email"],
            note="; ".join(note_parts),
            provider_batch_id=provider_reference,
            provider_message_id=data["message_id"],
            created_by=actor,
            created_at=data["received_at"],
        )
        return True
    payment = _match_payment(data)
    if payment_id and (payment is None or str(payment.pk) != str(payment_id)):
        return False
    if payment is None:
        return False
    payment.status = PayPalPayment.Status.COMPLETED
    payment.provider_reference = data["reference"] or data["message_id"]
    payment.provider_message_id = data["message_id"]
    LedgerEntry.objects.create(
        user=payment.user,
        amount_cents=payment.amount_cents,
        kind=LedgerEntry.Kind.PAYPAL,
        payment=payment,
        method="PAYPAL",
        note="PayPal Guthaben" if payment.purpose == "TOPUP" else "PayPal Ausgleich",
        reference=f"paypal:mail:{payment.provider_reference}",
        created_at=data["received_at"],
    )
    payment.save(
        update_fields=["status", "provider_reference", "provider_message_id", "updated_at"]
    )
    return True


def sync_mailbox(payment_id=None):
    if not mailbox_enabled():
        raise PayPalError("PayPal-IMAP ist nicht konfiguriert.")
    mode = (
        "SSL"
        if settings.PAYPAL_IMAP_USE_SSL
        else "STARTTLS"
        if settings.PAYPAL_IMAP_USE_TLS
        else "plain"
    )
    logger.debug(
        "PayPal-IMAP: Verbindung zu %s:%s über %s, Ordner %s",
        settings.PAYPAL_IMAP_HOST,
        settings.PAYPAL_IMAP_PORT,
        mode,
        settings.PAYPAL_IMAP_FOLDER,
    )
    mailbox = None
    try:
        if settings.PAYPAL_IMAP_USE_SSL:
            mailbox = imaplib.IMAP4_SSL(settings.PAYPAL_IMAP_HOST, settings.PAYPAL_IMAP_PORT)
        else:
            mailbox = imaplib.IMAP4(settings.PAYPAL_IMAP_HOST, settings.PAYPAL_IMAP_PORT)
            if settings.PAYPAL_IMAP_USE_TLS:
                mailbox.starttls()
        mailbox.login(settings.PAYPAL_IMAP_USER, settings.PAYPAL_IMAP_PASSWORD)
        logger.debug("PayPal-IMAP: Anmeldung erfolgreich")
        mailbox.select(settings.PAYPAL_IMAP_FOLDER)
        status, result = mailbox.search(None, "ALL")
        if status != "OK":
            raise PayPalError("PayPal-E-Mails konnten nicht gelesen werden.")
        logger.debug("PayPal-IMAP: %s Nachrichten gefunden", len(result[0].split()))
        changed = 0
        for message_id in result[0].split():
            status, fetched = mailbox.fetch(message_id, "(RFC822)")
            if status != "OK":
                logger.debug("PayPal-IMAP: Nachricht %s konnte nicht geladen werden", message_id)
                continue
            raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
            data = parse_message(raw, message_id.decode()) if raw else None
            if data:
                logger.debug(
                    "PayPal-IMAP: Nachricht %s erkannt: Richtung=%s Betrag=%s Referenz=%s",
                    message_id,
                    "Ausgang" if data["outgoing"] else "Eingang",
                    data["amount_cents"],
                    data["reference"] or "-",
                )
            if payment_id and data:
                belongs_to_payment = PayPalPayment.objects.filter(
                    pk=payment_id, provider_message_id=data["message_id"]
                ).exists()
                is_pending_payment = PayPalPayment.objects.filter(
                    pk=payment_id,
                    status__in=[PayPalPayment.Status.CREATED, PayPalPayment.Status.PENDING],
                    amount_cents=data["amount_cents"],
                ).exists()
                if not belongs_to_payment and not is_pending_payment:
                    continue
            changed += int(_apply_message(data, payment_id=payment_id))
            if data:
                target = _target_folder(data)
                if target:
                    _move_message(mailbox, message_id, target)
        mailbox.expunge()
        logger.debug("PayPal-IMAP: Scan abgeschlossen, %s Buchungen verarbeitet", changed)
        return changed
    except (imaplib.IMAP4.error, OSError) as error:
        logger.exception("PayPal-IMAP: Verbindungs- oder Lesevorgang fehlgeschlagen")
        raise PayPalError("PayPal-IMAP konnte nicht abgefragt werden.") from error
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def sync_payment(payment_id):
    sync_mailbox(payment_id)
    return PayPalPayment.objects.get(pk=payment_id)
