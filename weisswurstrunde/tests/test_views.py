import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from weisswurstrunde.models import (
    DefaultItem,
    Event,
    EventType,
    LedgerEntry,
    Order,
    PayPalPayment,
    Product,
    User,
)
from weisswurstrunde.services import manual_payment, save_order


@override_settings(INVITATION_CODE="invite", ALLOWED_HOSTS=["testserver"])
class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "test@example.org",
            "a-long-test-password",
            name="Stefan",
            paypal_email="paypal@example.org",
        )
        self.other = User.objects.create_user(
            "other@example.org",
            "a-long-test-password",
            name="Thomas",
            paypal_email="other-paypal@example.org",
        )
        self.product = Product.objects.create(name="Weisswurst", price_cents=160)
        self.event = Event.objects.create(
            date=timezone.localdate() + timedelta(days=3),
            deadline=timezone.now() + timedelta(days=2),
        )
        self.order = Order.objects.create(user=self.user, event=self.event)

    def test_all_protected_pages_and_rendering(self):
        paths = [
            "/",
            "/orders/",
            "/participants/",
            "/profile/",
            "/products/",
            "/payments/",
            "/history/",
            f"/history/{self.other.pk}/",
            f"/orders/{self.order.pk}/",
        ]
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 302, path)
        self.client.force_login(self.user)
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
            self.assertIn("https://www.sandbox.paypal.com", response["Content-Security-Policy"])

    def test_dashboard_shows_event_photo_and_distinct_upcoming_icons(self):
        self.client.force_login(self.user)
        for offset in [1, 8]:
            event = Event.objects.create(
                event_type=EventType.LEBERKAESE,
                date=timezone.localdate() + timedelta(days=offset),
                deadline=timezone.now() + timedelta(hours=1),
            )
            Order.objects.create(user=self.user, event=event)
        response = self.client.get("/")
        self.assertContains(response, 'src="/static/leberkaese.jpg"')
        self.assertContains(response, "Foto: Kobako")
        self.assertContains(response, 'data-lucide="utensils"')
        self.assertContains(response, 'data-lucide="sandwich"')
        self.event.date = timezone.localdate()
        self.event.save()
        self.assertContains(self.client.get("/"), 'src="/static/breakfast.jpg"')

    def test_all_balances_exclude_future_orders(self):
        self.client.force_login(self.user)
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        deposit = manual_payment(self.user, 2000, "CASH", "Deposit", uuid.uuid4())
        future_payment = LedgerEntry.objects.create(
            user=self.user,
            amount_cents=500,
            kind=LedgerEntry.Kind.MANUAL,
            reference="future-payment",
            created_at=timezone.now() + timedelta(days=1),
        )
        for due, expected in [(False, 2000), (True, 1680)]:
            with self.subTest(due=due):
                if due:
                    self.event.date = timezone.localdate()
                    self.event.save()
                for path in ["/", "/payments/", "/history/", f"/history/{self.user.pk}/"]:
                    response = self.client.get(path)
                    self.assertEqual(response.context["balance"], expected)
                response = self.client.get("/participants/")
                participant = response.context["participants"].get(pk=self.user.pk)
                self.assertEqual(participant.current_balance, expected)
                entries = self.client.get("/history/").context["entries"].object_list
                self.assertEqual(len(entries), 2 if due else 1)
                self.assertNotIn(future_payment.pk, [entry.pk for entry in entries])
                self.assertIn(deposit.pk, [entry.pk for entry in entries])
                self.assertEqual(entries[0].running_balance, expected)

    def test_profile_and_defaults_only_change_current_user(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/profile/",
            {
                "action": "defaults",
                **{
                    f"defaults-product_{product.pk}": 0
                    for product in Product.objects.exclude(pk=self.product.pk)
                },
                f"defaults-product_{self.product.pk}": 2,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DefaultItem.objects.get(user=self.user).quantity, 2)
        self.assertEqual(self.order.total, 0)
        response = self.client.post(
            "/profile/",
            {
                "action": "profile",
                "profile-name": "New Name",
                "profile-email": "changed@example.org",
                "profile-paypal_email": "separate-paypal@example.org",
                "profile-current_password": "a-long-test-password",
                "user_id": self.other.pk,
                "is_admin": True,
                "profile-is_admin": True,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.user.name, "New Name")
        self.assertEqual(self.user.paypal_email, "separate-paypal@example.org")
        self.assertEqual(self.other.name, "Thomas")
        self.assertFalse(self.user.is_admin)

    def test_orders_overview_sums_products_across_participants(self):
        self.client.force_login(self.user)
        other_order = Order.objects.create(user=self.other, event=self.event)
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        save_order(other_order.pk, {self.product.pk: 3}, self.other)
        unused = Product.objects.create(name="Brezel", price_cents=90)
        self.product.active = False
        self.product.save()
        response = self.client.get("/orders/")
        self.assertIsNone(response.context["event"])
        self.assertEqual(response.context["count"], 10)
        self.assertEqual(response.context["products"], [self.product, unused])
        self.assertEqual(
            response.context["summaries"], [{"event": self.event, "quantities": [5, 0]}]
        )
        self.assertNotContains(response, "<th>Teilnehmer</th>")
        detail = self.client.get("/orders/", {"event": self.event.pk, "count": 20})
        self.assertEqual(detail.context["totals"], {self.product.name: 5})
        self.assertEqual(detail.context["grand_total"], 800)
        self.assertContains(detail, "<th>Teilnehmer</th>")
        self.assertContains(detail, "?count=20")

    def test_orders_overview_limits_and_sorts_upcoming_dates(self):
        self.client.force_login(self.user)
        today = timezone.localdate()
        Event.objects.create(date=today - timedelta(days=1), deadline=timezone.now())
        Event.objects.bulk_create(
            [
                Event(date=today + timedelta(weeks=offset), deadline=timezone.now())
                for offset in range(1, 105)
            ]
        )
        for count in (10, 20, 50, 100):
            with self.subTest(count=count):
                response = self.client.get("/orders/", {"count": count})
                dates = [row["event"].date for row in response.context["summaries"]]
                self.assertEqual(len(dates), count)
                self.assertEqual(dates, sorted(dates))
                self.assertTrue(all(date >= today for date in dates))
        for invalid in ("abc", "-1", "0", "11", "1000"):
            response = self.client.get("/orders/", {"count": invalid})
            self.assertEqual(response.context["count"], 10)
            self.assertEqual(len(response.context["summaries"]), 10)

    def test_leberkaese_forms_and_overview_only_use_matching_products(self):
        self.client.force_login(self.user)
        product = Product.objects.get(event_type=EventType.LEBERKAESE)
        event = Event.objects.create(
            date=self.event.date,
            deadline=self.event.deadline,
            event_type=EventType.LEBERKAESE,
        )
        order = Order.objects.create(user=self.user, event=event)
        response = self.client.get(reverse("edit_order", args=[order.pk]))
        self.assertEqual(response.context["form"].products, [product])
        response = self.client.post(
            reverse("edit_order", args=[order.pk]),
            {
                f"order-{order.pk}-version": 0,
                f"order-{order.pk}-product_{product.pk}": 2,
                f"order-{order.pk}-product_{self.product.pk}": 99,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.items.get().product, product)
        self.assertEqual(self.user.balance, 0)
        response = self.client.get("/orders/", {"event_type": EventType.LEBERKAESE})
        self.assertEqual(response.context["products"], [product])
        self.assertEqual(response.context["summaries"], [{"event": event, "quantities": [2]}])
        self.assertContains(response, "Leberk\u00e4se")
        self.assertContains(response, "event_type=LEBERKAESE")
        response = self.client.get("/orders/", {"event": event.pk})
        self.assertEqual(response.context["totals"], {"Leberkassemmel": 2})
        self.assertEqual(response.context["grand_total"], 400)
        response = self.client.get("/")
        for context in response.context["upcoming"]:
            expected = product if context["order"].event_id == event.pk else self.product
            self.assertEqual(context["form"].products, [expected])

    def test_orders_empty_overview_and_past_event_details(self):
        self.client.force_login(self.user)
        self.event.date = timezone.localdate() - timedelta(days=1)
        self.event.save()
        response = self.client.get("/orders/")
        self.assertContains(response, "Keine kommenden Termine.")
        self.assertEqual(response.context["summaries"], [])
        response = self.client.get("/orders/", {"event": self.event.pk})
        self.assertEqual(response.context["event"], self.event)
        self.assertContains(response, "<th>Teilnehmer</th>")

    def test_password_change_preserves_session(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/profile/",
            {
                "action": "password",
                "password-old_password": "a-long-test-password",
                "password-new_password1": "1",
                "password-new_password2": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1"))
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_paypal_actions_require_ownership_and_post(self):
        payment = PayPalPayment.objects.create(user=self.other, amount_cents=1000, purpose="TOPUP")
        self.client.force_login(self.user)
        for action in ["sync", "retry", "close"]:
            path = f"/paypal/{payment.pk}/{action}/"
            self.assertEqual(self.client.post(path).status_code, 404)
            self.assertEqual(self.client.get(path).status_code, 405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f"/paypal/{payment.pk}/close/").status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "CANCELLED")

    def test_login_logout_and_invalid_login(self):
        self.assertEqual(self.client.get("/login/").status_code, 200)
        response = self.client.post("/login/", {"email": self.user.email, "password": "wrong"})
        self.assertContains(response, "nicht korrekt")
        response = self.client.post(
            "/login/", {"email": "TEST@example.org", "password": "a-long-test-password"}
        )
        self.assertRedirects(response, "/")
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertRedirects(self.client.post("/logout/"), "/login/")

    def test_registration_creates_orders_and_rejects_duplicate(self):
        data = {
            "name": "New",
            "email": "new@example.org",
            "paypal_email": "separate@example.org",
            "password": "strong-test-password-123",
            "password_confirm": "strong-test-password-123",
            "invitation": "invite",
            "is_admin": True,
        }
        response = self.client.post("/register/", data)
        self.assertRedirects(response, "/")
        user = User.objects.get(email="new@example.org")
        self.assertFalse(user.is_admin)
        self.assertTrue(Order.objects.filter(user=user, event=self.event).exists())
        self.client.logout()
        data["email"] = "NEW@example.org"
        self.assertEqual(self.client.post("/register/", data).status_code, 200)
        self.assertEqual(User.objects.filter(email__iexact="new@example.org").count(), 1)

    def test_admin_can_add_and_edit_orders_after_deadline(self):
        self.user.is_admin = True
        self.user.save()
        self.client.force_login(self.user)
        for status in Event.Status.values:
            with self.subTest(status=status):
                event = Event.objects.create(
                    date=self.event.date - timedelta(days=10 + Event.Status.values.index(status)),
                    deadline=timezone.now() - timedelta(days=2),
                    status=status,
                )
                path = reverse("add_order", args=[event.pk])
                detail = self.client.get("/orders/", {"event": event.pk})
                self.assertContains(detail, f'action="{path}"')
                self.assertIn(
                    self.other, detail.context["add_order_form"].fields["participant"].queryset
                )
                self.assertEqual(self.client.get(path).status_code, 405)
                response = self.client.post(path, {"participant": self.other.pk})
                order = Order.objects.get(user=self.other, event=event)
                self.assertRedirects(response, reverse("edit_order", args=[order.pk]))
                self.assertFalse(order.items.exists())
                self.assertFalse(order.charges.exists())
                response = self.client.post(
                    reverse("edit_order", args=[order.pk]),
                    {
                        f"order-{order.pk}-version": 0,
                        f"order-{order.pk}-product_{self.product.pk}": 2,
                    },
                )
                self.assertRedirects(response, f"/orders/?event={event.pk}")
                self.client.post(path, {"participant": self.other.pk})
                self.assertEqual(Order.objects.filter(user=self.other, event=event).count(), 1)
                self.assertEqual(order.total, 320)
                self.assertEqual(order.charges.count(), 1)
                self.assertEqual(order.charges.get().recorded_by, self.user)
                detail = self.client.get("/orders/", {"event": event.pk})
                self.assertNotIn(
                    self.other, detail.context["add_order_form"].fields["participant"].queryset
                )
                event.refresh_from_db()
                self.assertEqual(event.status, status)

    def test_add_order_rejects_non_admin_invalid_participants_and_missing_csrf(self):
        path = reverse("add_order", args=[self.event.pk])
        count = Order.objects.count()
        self.assertEqual(self.client.post(path, {"participant": self.other.pk}).status_code, 302)
        self.client.force_login(self.user)
        self.assertNotContains(
            self.client.get("/orders/", {"event": self.event.pk}), f'action="{path}"'
        )
        self.assertEqual(
            self.client.post(path, {"participant": self.other.pk, "is_admin": True}).status_code,
            403,
        )
        self.user.is_admin = True
        self.user.save()
        self.other.is_active = False
        self.other.save()
        for participant in ["", "invalid", 999999, self.other.pk]:
            response = self.client.post(path, {"participant": participant})
            self.assertRedirects(response, f"/orders/?event={self.event.pk}")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(path, {"participant": self.user.pk}).status_code, 403)
        self.assertEqual(Order.objects.count(), count)

    def test_only_admin_can_edit_locked_orders(self):
        self.event.status = Event.Status.LOCKED
        self.event.deadline = timezone.now() - timedelta(days=1)
        self.event.save()
        path = reverse("edit_order", args=[self.order.pk])
        field = f"order-{self.order.pk}-product_{self.product.pk}"
        data = {f"order-{self.order.pk}-version": 0, field: 2, "is_admin": True}
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(path), f'name="{field}"')
        self.assertEqual(self.client.post(path, data).status_code, 200)
        self.assertEqual(self.order.total, 0)
        self.assertFalse(self.order.charges.exists())
        self.other.is_admin = True
        self.other.save()
        own_order = Order.objects.create(user=self.other, event=self.event)
        self.client.force_login(self.other)
        response = self.client.get(path)
        self.assertContains(response, f'name="{field}"')
        self.assertContains(response, "Admin-Korrektur")
        self.assertContains(
            self.client.get("/"), f'name="order-{own_order.pk}-product_{self.product.pk}"'
        )
        self.assertContains(
            self.client.get("/orders/", {"event": self.event.pk}),
            f'aria-label="Bestellung von {self.user.name} bearbeiten"',
        )
        self.assertEqual(self.client.post(path, data).status_code, 302)
        self.assertEqual(self.user.balance, 0)
        self.assertEqual(self.order.charges.get().recorded_by, self.other)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.LOCKED)

    def test_any_participant_can_edit_any_open_order(self):
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("edit_order", args=[self.order.pk]),
            {
                f"order-{self.order.pk}-version": 0,
                f"order-{self.order.pk}-product_{self.product.pk}": 2,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.user.balance, 0)
        self.event.deadline = timezone.now() - timedelta(seconds=1)
        self.event.save()
        response = self.client.post(
            reverse("edit_order", args=[self.order.pk]),
            {
                f"order-{self.order.pk}-version": 1,
                f"order-{self.order.pk}-product_{self.product.pk}": 3,
            },
        )
        self.assertContains(response, "Bestellschluss")
        self.assertEqual(self.user.balance, 0)

    def test_invalid_ids_and_csrf(self):
        self.client.force_login(self.user)
        for path in [
            "/orders/99999/",
            "/orders/not-an-id/",
            "/history/99999/",
            "/paypal/not-a-uuid/sync/",
        ]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/orders/?event=not-an-id").status_code, 200)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post("/payments/", {"action": "manual"}).status_code, 403)

    def test_manual_payment_retry_and_mismatched_retry(self):
        self.client.force_login(self.user)
        data = {
            "action": "manual",
            "manual-amount": "20.00",
            "manual-method": "CASH",
            "manual-request_id": str(uuid.uuid4()),
        }
        self.assertEqual(self.client.post("/payments/", data).status_code, 302)
        self.assertEqual(self.client.post("/payments/", data).status_code, 302)
        self.assertEqual(self.user.balance, 2000)
        data["manual-amount"] = "15.00"
        self.assertContains(self.client.post("/payments/", data), "bereits anders verbucht")
        self.assertEqual(self.user.balance, 2000)

    def test_price_changes_do_not_reprice_existing_orders(self):
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            "/products/",
            {
                "product_id": self.product.pk,
                "event_type": EventType.LEBERKAESE,
                "name": self.product.name,
                "unit": "Stueck",
                "price_cents": 200,
                "active": "on",
            },
        )
        self.assertRedirects(response, "/products/")
        self.assertEqual(self.order.total, 320)
        self.product.refresh_from_db()
        self.assertEqual(self.product.event_type, EventType.WEISSWURST)

    @patch("weisswurstrunde.views.paypal.verify_webhook", return_value=False)
    def test_invalid_webhook_rejected(self, verify):
        response = self.client.post(
            "/paypal/webhook/", {"id": "FAKE"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.user.balance, 0)
