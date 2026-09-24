import copy
import os
import uuid
from unittest import skipUnless
from unittest.mock import patch

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from weisswurstrunde.models import LedgerEntry, PayPalPayment, User
from weisswurstrunde.paypal import (
    PayPalClient,
    PayPalError,
    cancel_payment,
    create_payment,
    process_webhook,
    sync_payment,
    verify_webhook,
)
from weisswurstrunde.services import manual_payment, reconcile


@override_settings(
    PAYPAL_CLIENT_ID="test-client",
    PAYPAL_CLIENT_SECRET="test-secret",
    PAYPAL_MERCHANT_ID="MERCHANT",
    PAYPAL_WEBHOOK_ID="HOOK",
)
class PayPalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "test@example.org", "test-password", name="Test", paypal_email="different@example.org"
        )
        self.payment = PayPalPayment.objects.create(
            user=self.user, amount_cents=2000, purpose="TOPUP", order_id="ORDER"
        )
        self.data = {
            "id": "ORDER",
            "intent": "CAPTURE",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "custom_id": str(self.payment.pk),
                    "payee": {"merchant_id": "MERCHANT"},
                    "amount": {"currency_code": "EUR", "value": "20.00"},
                    "payments": {
                        "captures": [
                            {
                                "id": "CAPTURE",
                                "status": "COMPLETED",
                                "amount": {"currency_code": "EUR", "value": "20.00"},
                            }
                        ]
                    },
                }
            ],
        }

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_verified_credit_and_duplicate_callback(self, request):
        request.return_value = self.data
        sync_payment(self.payment.pk)
        sync_payment(self.payment.pk)
        process_webhook({"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "CAPTURE"}})
        self.assertEqual(self.user.balance, 2000)
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(reconcile(), [])

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_amount_currency_owner_and_merchant_mismatches(self, request):
        for key, value in [
            ("custom_id", "wrong"),
            ("payee", {"merchant_id": "WRONG"}),
            ("amount", {"currency_code": "USD", "value": "20.00"}),
            ("amount", {"currency_code": "EUR", "value": "19.00"}),
        ]:
            data = copy.deepcopy(self.data)
            data["purchase_units"][0][key] = value
            request.return_value = data
            with self.assertRaises(PayPalError):
                sync_payment(self.payment.pk)
        self.assertEqual(self.user.balance, 0)

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_pending_failed_then_delayed_completion(self, request):
        for provider_status, expected in [
            ("PENDING", "PENDING"),
            ("DECLINED", "FAILED"),
            ("COMPLETED", "COMPLETED"),
        ]:
            self.data["purchase_units"][0]["payments"]["captures"][0]["status"] = provider_status
            request.return_value = self.data
            result = sync_payment(self.payment.pk)
            self.assertEqual(result.status, expected)
        self.assertEqual(self.user.balance, 2000)

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_partial_and_full_refunds_are_append_only(self, request):
        request.return_value = self.data
        sync_payment(self.payment.pk)
        payments = self.data["purchase_units"][0]["payments"]
        payments["refunds"] = [
            {
                "id": "REFUND1",
                "status": "COMPLETED",
                "amount": {"currency_code": "EUR", "value": "5.00"},
            }
        ]
        sync_payment(self.payment.pk)
        sync_payment(self.payment.pk)
        self.assertEqual(self.user.balance, 1500)
        payments["refunds"].append(
            {
                "id": "REFUND2",
                "status": "COMPLETED",
                "amount": {"currency_code": "EUR", "value": "15.00"},
            }
        )
        result = sync_payment(self.payment.pk)
        self.assertEqual(result.status, "REFUNDED")
        self.assertEqual(self.user.balance, 0)
        self.assertEqual(LedgerEntry.objects.count(), 3)
        self.assertEqual(reconcile(), [])

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_webhook_verification(self, request):
        headers = {
            name: "value"
            for name in [
                "PAYPAL-AUTH-ALGO",
                "PAYPAL-CERT-URL",
                "PAYPAL-TRANSMISSION-ID",
                "PAYPAL-TRANSMISSION-SIG",
                "PAYPAL-TRANSMISSION-TIME",
            ]
        }
        request.return_value = {"verification_status": "FAILURE"}
        self.assertFalse(verify_webhook(headers, {"id": "EVENT"}))
        request.return_value = {"verification_status": "SUCCESS"}
        self.assertTrue(verify_webhook(headers, {"id": "EVENT"}))
        self.assertFalse(verify_webhook({}, {}))

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_outstanding_is_server_calculated_and_rechecked(self, request):
        LedgerEntry.objects.create(
            user=self.user, kind="ORDER", amount_cents=-1360, reference="test-order"
        )
        request.return_value = {
            "id": "DEBT-ORDER",
            "links": [
                {
                    "rel": "payer-action",
                    "href": "https://www.sandbox.paypal.com/checkoutnow?token=DEBT-ORDER",
                }
            ],
        }
        payment = create_payment(self.user, 99999, "DEBT", uuid.uuid4())
        self.assertEqual(payment.amount_cents, 1360)
        manual_payment(self.user, 500, "CASH", "", uuid.uuid4())
        request.return_value = {
            "id": "DEBT-ORDER",
            "intent": "CAPTURE",
            "status": "APPROVED",
            "purchase_units": [
                {
                    "custom_id": str(payment.pk),
                    "payee": {"merchant_id": "MERCHANT"},
                    "amount": {"currency_code": "EUR", "value": "13.60"},
                }
            ],
        }
        with self.assertRaises(ValidationError):
            sync_payment(payment.pk, capture=True)

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_cancelled_payment_does_not_credit(self, request):
        self.data["status"] = "VOIDED"
        self.data["purchase_units"][0].pop("payments")
        request.return_value = self.data
        payment = sync_payment(self.payment.pk)
        self.assertEqual(payment.status, "CANCELLED")
        self.assertEqual(self.user.balance, 0)

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_approved_payment_capture_then_verify(self, request):
        approved = copy.deepcopy(self.data)
        approved["status"] = "APPROVED"
        approved["purchase_units"][0].pop("payments")
        request.side_effect = [approved, {}, self.data]
        sync_payment(self.payment.pk, capture=True)
        self.assertEqual(self.user.balance, 2000)
        self.assertEqual(
            request.call_args_list[1].args[:2], ("POST", "/v2/checkout/orders/ORDER/capture")
        )

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_closed_checkout_never_captures_but_recognizes_late_receipt(self, request):
        approved = copy.deepcopy(self.data)
        approved["status"] = "APPROVED"
        approved["purchase_units"][0].pop("payments")
        request.return_value = approved
        self.assertEqual(cancel_payment(self.payment.pk).status, "CANCELLED")
        sync_payment(self.payment.pk, capture=True)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))
        self.assertEqual(self.user.balance, 0)
        request.return_value = self.data
        sync_payment(self.payment.pk)
        self.assertEqual(self.user.balance, 2000)
        with self.assertRaises(ValidationError):
            cancel_payment(self.payment.pk)

    @patch("weisswurstrunde.paypal.PayPalClient.request")
    def test_same_capture_cannot_credit_different_payment(self, request):
        request.return_value = self.data
        sync_payment(self.payment.pk)
        other = PayPalPayment.objects.create(
            user=self.user, amount_cents=2000, purpose="TOPUP", order_id="OTHER"
        )
        self.data["id"] = "OTHER"
        self.data["purchase_units"][0]["custom_id"] = str(other.pk)
        with self.assertRaises(PayPalError):
            sync_payment(other.pk)
        self.assertEqual(self.user.balance, 2000)

    def test_http_adapter_uses_server_oauth_and_request_id(self):
        calls = []

        def respond(request):
            calls.append(request)
            if request.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "test-access-token"})
            return httpx.Response(200, json={"id": "SERVER-ORDER"})

        client = httpx.Client(
            base_url="https://api-m.sandbox.paypal.com", transport=httpx.MockTransport(respond)
        )
        with patch("weisswurstrunde.paypal.httpx.Client", return_value=client):
            result = PayPalClient().request(
                "POST", "/v2/checkout/orders", {}, request_id="test-idempotency"
            )
        self.assertEqual(result["id"], "SERVER-ORDER")
        self.assertTrue(calls[0].headers["Authorization"].startswith("Basic "))
        self.assertEqual(calls[1].headers["Authorization"], "Bearer test-access-token")
        self.assertEqual(calls[1].headers["PayPal-Request-Id"], "test-idempotency")


@skipUnless(
    os.environ.get("RUN_PAYPAL_SANDBOX_TESTS") == "true", "Real sandbox credentials not enabled"
)
class SandboxSmokeTests(TestCase):
    def test_create_sandbox_checkout_without_capture(self):
        self.assertEqual(settings.PAYPAL_ENVIRONMENT, "sandbox")
        self.assertEqual(settings.PAYPAL_API_BASE, "https://api-m.sandbox.paypal.com")
        user = User.objects.create_user(
            "sandbox-test@example.test",
            "sandbox-test-password",
            name="Sandbox",
            paypal_email="sandbox-buyer@example.test",
        )
        payment = create_payment(user, 100, "TOPUP", uuid.uuid4())
        self.assertTrue(payment.order_id)
        self.assertTrue(payment.approval_url.startswith("https://www.sandbox.paypal.com/"))
        self.assertEqual(user.balance, 0)
