import email
import hashlib
import html
import imaplib
import logging
import re
from decimal import Decimal, InvalidOperation
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from .audit import record
from .models import LedgerEntry, Payout, PayPalPayment, User

logger = logging.getLogger(__name__)
PRESET_AMOUNTS_CENTS = (1000, 2000, 5000)
VERIFICATION_AMOUNT_CENTS = 1


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


def paypal_me_url(amount_cents):
    if not enabled():
        return ""
    amount = str(amount_cents // 100) if amount_cents % 100 == 0 else money(amount_cents)
    return f"{settings.PAYPAL_ME_LINK.rstrip('/')}/{amount}"


_paypal_me_url = paypal_me_url


def payment_url(user, amount_cents):
    if not enabled():
        raise PayPalError("PayPal.me ist noch nicht konfiguriert.")
    if not user.paypal_username:
        raise ValidationError("Bitte hinterlege zuerst deinen PayPal-Benutzernamen im Profil.")
    if not user.paypal_verified:
        raise ValidationError(
            "Bitte validiere zuerst deinen PayPal-Benutzernamen mit einer Testueberweisung von 0,01 EUR."
        )
    if (
        not isinstance(amount_cents, int)
        or isinstance(amount_cents, bool)
        or not 0 < amount_cents <= 1000000
    ):
        raise ValidationError("Der Betrag muss zwischen 0,01 und 10.000,00 EUR liegen.")
    return paypal_me_url(amount_cents)


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


def _strip_scripts(text):
    return re.sub(
        r"<(?:style|script)\b[^>]*>.*?</(?:style|script)>",
        " ",
        text,
        flags=re.I | re.S,
    )


def _plain_text(text):
    text = _strip_scripts(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _segments(text):
    text = _strip_scripts(text)
    raw_lines = re.split(r"<[^>]+>|\r?\n", text)
    segments = []
    for line in raw_lines:
        cleaned = re.sub(r"\s+", " ", html.unescape(line)).strip()
        if cleaned:
            segments.append(cleaned)
    return segments


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


def _clean_sender(value):
    cleaned = re.sub(r"^(?:paypal\s*[:\-–]\s*)", "", value or "", flags=re.IGNORECASE)
    return " ".join(cleaned.strip(" :,-.!").split()).lstrip("@")


def _sender(segments, text):
    patterns = (
        r"^(.+?)\s+hat\s+(?:dir|ihnen)\b.*?\bgesendet\b",
        r"^(.+?)\s+sent\s+you\b",
        r"\bvon\s+(.+?)\s+erhalten\b",
        r"\byou\s+received\b.*?\bfrom\s+(.+?)(?=\s+(?:transaction|transaktions)|[.!]|$)",
    )
    for pattern in patterns:
        for segment in [*segments, text]:
            match = re.search(pattern, segment, re.IGNORECASE)
            if match:
                candidate = _clean_sender(match.group(1))
                if candidate and not re.search(
                    r"\b(?:sie\s+haben|du\s+hast|you\s+sent)\b", candidate, re.IGNORECASE
                ):
                    return candidate
    return ""


def parse_message(raw, message_id):
    message = email.message_from_bytes(raw, policy=policy.default)
    subject = _decode(message.get("Subject")).strip()
    body = _body(message)
    combined = f"{subject}\n{body}"
    text = _plain_text(combined)
    segments = _segments(combined)
    outgoing = bool(
        re.search(
            r"\byou sent\b|\b(?:sie haben|du hast)\s+(?:eine\s+zahlung|\d[\d.,]*\s*(?:€\s*)?(?:EUR|€)\s+an\s+[^.!?\n]+?)\s+gesendet\b",
            text,
            re.IGNORECASE,
        )
    )
    incoming = not outgoing and bool(
        re.search(
            r"\byou received\b|\b(?:sie haben|du hast)\b[^.!?\n]*?\berhalten\b|\bzahlung erhalten\b|\bhat\s+(?:dir|ihnen)\b[^.!?\n]*?\bgesendet\b|\bsent\s+you\b",
            text,
            re.IGNORECASE,
        )
    )
    if not (incoming or outgoing):
        return None
    recipient_name, recipient_email = _recipient(text) if outgoing else ("", "")
    sender_name = "" if outgoing else _sender(segments, text)
    try:
        received_at = parsedate_to_datetime(message.get("Date"))
    except (TypeError, ValueError, IndexError, OverflowError):
        received_at = timezone.now()
    if received_at.tzinfo is None:
        received_at = timezone.make_aware(received_at)
    return {
        "message_id": (message.get("Message-ID") or str(message_id)).strip(),
        "subject": subject[:255],
        "amount_cents": _amount(text),
        "reference": _reference(text),
        "sender_name": sender_name[:180],
        "recipient_name": recipient_name,
        "recipient_email": recipient_email,
        "text": text.lower(),
        "outgoing": outgoing,
        "received_at": received_at,
    }


def _incoming_folder(user):
    return f"Einzahlungen/{user.pk}-{slugify(user.name) or 'user'}"


def _target_folder(data):
    payment = (
        PayPalPayment.objects.select_related("user")
        .filter(
            provider_message_id=data["message_id"],
            status=PayPalPayment.Status.COMPLETED,
            user__isnull=False,
        )
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


def _complete_payment_entry(user, payment):
    payment.user = user
    payment.status = PayPalPayment.Status.COMPLETED
    payment.save(
        update_fields=[
            "user",
            "sender_name",
            "subject",
            "status",
            "provider_reference",
            "provider_message_id",
            "updated_at",
        ]
    )
    LedgerEntry.objects.create(
        user=user,
        amount_cents=payment.amount_cents,
        kind=LedgerEntry.Kind.PAYPAL,
        payment=payment,
        method="PAYPAL",
        note="PayPal Guthaben",
        reference=f"paypal:mail:{payment.provider_reference}",
        created_at=payment.created_at,
    )
    record(
        "paypal_payment_completed",
        user,
        payment_id=payment.pk,
        amount_cents=payment.amount_cents,
        provider_reference=payment.provider_reference,
    )


@transaction.atomic
def match_unmatched_for_user(user):
    user = User.objects.select_for_update().get(pk=user.pk)
    if not user.is_active or not user.paypal_username:
        return 0
    unmatched = list(
        PayPalPayment.objects.select_for_update()
        .filter(
            status=PayPalPayment.Status.UNMATCHED,
            sender_name__iexact=user.paypal_username,
        )
        .order_by("created_at", "pk")
    )
    if not unmatched:
        return 0
    if not user.paypal_verified:
        if not any(
            payment.amount_cents == VERIFICATION_AMOUNT_CENTS for payment in unmatched
        ):
            return 0
        user.paypal_verified = True
        user.save(update_fields=["paypal_verified"])
        record(
            "paypal_username_verified",
            user,
            user_id=user.pk,
            paypal_username=user.paypal_username,
        )
    for payment in unmatched:
        _complete_payment_entry(user, payment)
    return len(unmatched)


def _find_matching_user(data):
    sender_name = _clean_sender(data.get("sender_name", ""))
    if sender_name:
        return (
            User.objects.select_for_update()
            .filter(is_active=True, paypal_username__iexact=sender_name)
            .exclude(paypal_username="")
            .first()
        )
    return None


@transaction.atomic
def _apply_message(data):
    if not data or not data["amount_cents"]:
        return False
    if data["outgoing"]:
        provider_reference = (
            data["reference"] or hashlib.sha256(data["message_id"].encode()).hexdigest()
        )
        if (
            Payout.objects.filter(provider_message_id=data["message_id"]).exists()
            or Payout.objects.filter(provider_batch_id=provider_reference).exists()
        ):
            return False
        actor = User.objects.filter(is_admin=True, is_active=True).order_by("pk").first()
        note_parts = []
        if data["recipient_name"]:
            note_parts.append(f"Empfänger: {data['recipient_name']}")
        if data["reference"]:
            note_parts.append(f"Transaktionscode: {data['reference']}")
        payout = Payout.objects.create(
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
        record(
            "paypal_payout_completed",
            actor,
            payout_id=payout.pk,
            amount_cents=payout.amount_cents,
            provider_reference=provider_reference,
        )
        return True

    provider_reference = data["reference"] or data["message_id"]
    existing = (
        PayPalPayment.objects.select_for_update()
        .filter(
            Q(provider_message_id=data["message_id"])
            | Q(provider_reference=provider_reference)
        )
        .first()
    )
    if existing and existing.status != PayPalPayment.Status.UNMATCHED:
        return False

    sender_name = _clean_sender(data.get("sender_name", ""))
    subject = (data.get("subject") or "")[:255]
    if existing is None:
        payment = PayPalPayment.objects.create(
            user=None,
            sender_name=sender_name[:180],
            subject=subject,
            amount_cents=data["amount_cents"],
            purpose="TOPUP",
            status=PayPalPayment.Status.UNMATCHED,
            provider_reference=provider_reference,
            provider_message_id=data["message_id"],
            created_at=data["received_at"],
        )
    else:
        payment = existing
        payment.sender_name = sender_name[:180] or payment.sender_name
        payment.subject = subject or payment.subject

    user = _find_matching_user(data)
    if user is None:
        if existing is None:
            record(
                "paypal_payment_unmatched",
                payment_id=payment.pk,
                sender_name=payment.sender_name,
                amount_cents=payment.amount_cents,
                provider_reference=provider_reference,
            )
        else:
            payment.save(update_fields=["sender_name", "subject", "updated_at"])
        return False

    if not user.paypal_verified and data["amount_cents"] == VERIFICATION_AMOUNT_CENTS:
        user.paypal_verified = True
        user.save(update_fields=["paypal_verified"])
        record(
            "paypal_username_verified",
            user,
            user_id=user.pk,
            paypal_username=user.paypal_username,
        )

    if not user.paypal_verified:
        if existing is None:
            record(
                "paypal_payment_unmatched",
                user,
                payment_id=payment.pk,
                sender_name=payment.sender_name,
                amount_cents=payment.amount_cents,
                provider_reference=provider_reference,
            )
        else:
            payment.save(update_fields=["sender_name", "subject", "updated_at"])
        return False

    _complete_payment_entry(user, payment)
    match_unmatched_for_user(user)
    return True


def sync_mailbox():
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
                    "PayPal-IMAP: Nachricht %s erkannt: Richtung=%s Absender=%s Betrag=%s Referenz=%s",
                    message_id,
                    "Ausgang" if data["outgoing"] else "Eingang",
                    data["sender_name"] or "-",
                    data["amount_cents"],
                    data["reference"] or "-",
                )
            changed += int(_apply_message(data))
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

