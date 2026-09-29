from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from weisswurstrunde.models import LedgerEntry, Payout, PayPalPayment, User
from weisswurstrunde.paypal import _apply_message, create_payment, parse_message, sync_mailbox


@override_settings(
    PAYPAL_ME_LINK="https://paypal.me/stammtisch",
    PAYPAL_IMAP_HOST="imap.example.org",
    PAYPAL_IMAP_PORT=993,
    PAYPAL_IMAP_USER="paypal@example.org",
    PAYPAL_IMAP_PASSWORD="secret",
    PAYPAL_IMAP_FOLDER="INBOX",
)
class PayPalMailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "test@example.org", "test-password", name="Test", paypal_email="buyer@example.org"
        )

    def test_create_payment_uses_paypal_me_and_waits_for_mail(self):
        payment = create_payment(self.user, 2000, "TOPUP", "3d1c4a8c-3e2e-4bb0-9d0d-1cde75a0b3d5")
        self.assertEqual(payment.status, PayPalPayment.Status.PENDING)
        self.assertEqual(payment.approval_url, "https://paypal.me/stammtisch/20")

    def test_invalid_payment_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_payment(self.user, 0, "TOPUP", "3d1c4a8c-3e2e-4bb0-9d0d-1cde75a0b3d6")

    def test_incoming_mail_books_once(self):
        raw = (
            b"From: service@paypal.com\n"
            b"Subject: Sie haben eine Zahlung erhalten\n"
            b"Date: Tue, 29 Sep 2026 08:00:00 +0200\n"
            b"Message-ID: <mail-1@example.org>\n\n"
            b"Sie haben 20,00 EUR von buyer@example.org erhalten. "
            b"Transaktionscode: 9ABCD123456\n"
        )
        payment = create_payment(self.user, 2000, "TOPUP", "3d1c4a8c-3e2e-4bb0-9d0d-1cde75a0b3d7")
        data = parse_message(raw, "1")
        self.assertTrue(_apply_message(data))
        self.assertFalse(_apply_message(data))
        payment.refresh_from_db()
        self.assertEqual(payment.status, PayPalPayment.Status.COMPLETED)
        self.assertEqual(self.user.balance, 2000)
        self.assertEqual(LedgerEntry.objects.count(), 1)

    def test_outgoing_mail_completes_payout(self):
        payout = Payout.objects.create(
            amount_cents=1500,
            method=Payout.Method.PAYPAL,
            status=Payout.Status.PENDING,
            recipient="recipient@example.org",
            created_by=self.user,
        )
        data = {
            "message_id": "<mail-2@example.org>",
            "amount_cents": 1500,
            "reference": "OUT12345678",
            "text": "sie haben eine zahlung gesendet an recipient@example.org",
            "recipient_name": "Recipient Name",
            "recipient_email": "recipient@example.org",
            "outgoing": True,
            "received_at": payout.created_at,
        }
        self.assertTrue(_apply_message(data))
        payout.refresh_from_db()
        imported = Payout.objects.exclude(pk=payout.pk).get()
        self.assertEqual(imported.status, Payout.Status.COMPLETED)
        self.assertEqual(imported.provider_batch_id, "OUT12345678")
        self.assertIn("Recipient Name", imported.note)
        self.assertIn("OUT12345678", imported.note)

    def test_outgoing_mail_extracts_recipient_name_and_email(self):
        raw = (
            b"Subject: You sent a payment\n\n"
            b"You sent a payment to Max Mustermann (recipient@example.org) "
            b"for 15.00 EUR. Transaction ID: OUT12345678\n"
        )
        data = parse_message(raw, "3")
        self.assertEqual(data["recipient_name"], "Max Mustermann")
        self.assertEqual(data["recipient_email"], "recipient@example.org")
        self.assertEqual(data["reference"], "OUT12345678")

    def test_paypal_html_mail_extracts_visible_recipient_and_reference(self):
        raw = (
            b"Subject: Du hast eine Zahlung gesendet\n\n"
            b"<html><style>.x { color: red; }</style><body>"
            b"<p>Du hast 2,00&nbsp;&euro;&nbsp;EUR an Stefan Hackenberg gesendet</p>"
            b"<strong>Transaktionscode</strong><a>6DE43837HX011215V</a>"
            b"</body></html>"
        )
        data = parse_message(raw, "4")
        self.assertEqual(data["amount_cents"], 200)
        self.assertEqual(data["recipient_name"], "Stefan Hackenberg")
        self.assertEqual(data["recipient_email"], "")
        self.assertEqual(data["reference"], "6DE43837HX011215V")

    @patch("weisswurstrunde.paypal.imaplib.IMAP4_SSL")
    def test_mailbox_reads_messages(self, imap_class):
        raw = (
            b"From: service@paypal.com\nSubject: You received a payment\n"
            b"Message-ID: <mail-3@example.org>\n\n"
            b"You received 20.00 EUR from buyer@example.org.\n"
        )
        create_payment(self.user, 2000, "TOPUP", "3d1c4a8c-3e2e-4bb0-9d0d-1cde75a0b3d8")
        mailbox = imap_class.return_value
        mailbox.search.return_value = ("OK", [b"1"])
        mailbox.fetch.return_value = ("OK", [(b"header", raw), b")"])
        mailbox.move.return_value = ("OK", [b"1"])
        self.assertEqual(sync_mailbox(), 1)
        mailbox.move.assert_called_once_with(b"1", "Einzahlungen.1-test")
        self.assertEqual(sync_mailbox(), 0)

    @override_settings(PAYPAL_IMAP_USE_SSL=False, PAYPAL_IMAP_USE_TLS=True)
    @patch("weisswurstrunde.paypal.imaplib.IMAP4")
    def test_mailbox_can_use_starttls(self, imap_class):
        mailbox = imap_class.return_value
        mailbox.search.return_value = ("OK", [b""])
        self.assertEqual(sync_mailbox(), 0)
        mailbox.starttls.assert_called_once_with()
