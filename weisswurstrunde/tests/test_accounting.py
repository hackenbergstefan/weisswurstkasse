import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from weisswurstrunde.models import DefaultItem, Event, EventType, LedgerEntry, Order, Product, User
from weisswurstrunde.services import (
    generate_events,
    manual_payment,
    provision_order,
    reconcile,
    reverse_manual,
    save_defaults,
    save_order,
)


class AccountingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "stefan@example.org",
            "a-long-test-password",
            name="Stefan",
            paypal_email="paypal@example.org",
        )
        self.product = Product.objects.create(name="Weisswurst", unit="Stueck", price_cents=160)
        self.event = Event.objects.create(
            date=timezone.localdate() + timedelta(days=3),
            deadline=timezone.now() + timedelta(days=2),
        )
        self.order = Order.objects.create(user=self.user, event=self.event)

    def test_balance_includes_orders_only_from_their_event_date(self):
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        self.assertEqual(self.user.balance, 0)
        manual_payment(self.user, 2000, "CASH", "Deposit", uuid.uuid4())
        self.assertEqual(self.user.balance, 2000)
        LedgerEntry.objects.create(
            user=self.user,
            amount_cents=500,
            kind=LedgerEntry.Kind.MANUAL,
            reference="future-payment",
            created_at=timezone.now() + timedelta(days=2),
        )
        self.assertEqual(self.user.balance, 2000)
        self.event.date = timezone.localdate()
        self.event.save()
        self.assertEqual(self.user.balance, 1680)
        self.event.date -= timedelta(days=1)
        self.event.save()
        self.assertEqual(self.user.balance, 1680)
        self.event.date = timezone.localdate() + timedelta(days=1)
        self.event.save()
        save_order(self.order.pk, {self.product.pk: 0}, self.user)
        self.assertEqual(self.user.balance, 2000)
        self.assertEqual(reconcile(), [])

    def test_prices_snapshots_and_compensating_charges(self):
        self.event.date = timezone.localdate()
        self.event.save()
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        self.assertEqual(self.user.balance, -320)
        self.product.price_cents = 200
        self.product.save()
        self.assertEqual(Order.objects.get(pk=self.order.pk).total, 320)
        save_order(self.order.pk, {self.product.pk: 1}, self.user)
        self.assertEqual(list(self.user.ledger.values_list("amount_cents", flat=True)), [-320, 120])
        self.assertEqual(self.user.balance, -200)
        self.assertEqual(reconcile(), [])

    def test_zero_and_invalid_quantities(self):
        for invalid in [-1, 101, 1.5, True, "2"]:
            with self.assertRaises(ValidationError):
                save_order(self.order.pk, {self.product.pk: invalid}, self.user)
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        save_order(self.order.pk, {self.product.pk: 0}, self.user)
        self.assertEqual(self.user.balance, 0)

    def test_deadline_and_optimistic_lock(self):
        with self.assertRaises(ValidationError):
            save_order(self.order.pk, {self.product.pk: 2}, self.user, expected_version=3)
        self.event.deadline = timezone.now() - timedelta(seconds=1)
        self.event.save()
        with self.assertRaises(ValidationError):
            save_order(self.order.pk, {self.product.pk: 2}, self.user)

    def test_admin_can_correct_closed_orders_with_audited_charges(self):
        admin = User.objects.create_user(
            "admin@example.org",
            "admin-test-password",
            name="Admin",
            paypal_email="admin@example.org",
            is_admin=True,
        )
        for status in Event.Status.values:
            with self.subTest(status=status):
                self.event.status = status
                self.event.date = timezone.localdate() - timedelta(days=1)
                self.event.deadline = timezone.now() - timedelta(days=1)
                self.event.save()
                with self.assertRaises(ValidationError):
                    save_order(self.order.pk, {self.product.pk: 2}, self.user)
                self.order.refresh_from_db()
                version = self.order.version
                save_order(self.order.pk, {self.product.pk: 2}, admin, version)
                with self.assertRaises(ValidationError):
                    save_order(self.order.pk, {self.product.pk: 3}, admin, version)
                save_order(self.order.pk, {self.product.pk: 1}, admin)
                self.assertEqual(self.user.balance, -160)
                self.event.refresh_from_db()
                self.assertEqual(self.event.status, status)
                self.assertEqual(reconcile(), [])
        self.assertFalse(self.order.charges.exclude(recorded_by=admin).exists())
        admin.is_active = False
        admin.save()
        with self.assertRaises(ValidationError):
            save_order(self.order.pk, {self.product.pk: 2}, admin)

    def test_manual_payment_idempotence_and_reversal(self):
        token = uuid.uuid4()
        entry = manual_payment(self.user, 2000, "CASH", "Breakfast", token)
        manual_payment(self.user, 2000, "CASH", "Breakfast", token)
        self.assertEqual(self.user.balance, 2000)
        reverse_manual(entry.pk, self.user, "Wrong amount")
        reverse_manual(entry.pk, self.user, "Wrong amount")
        manual_payment(self.user, 1500, "BANK", "Correct amount", uuid.uuid4())
        self.assertEqual(self.user.balance, 1500)
        with self.assertRaises(ValidationError):
            manual_payment(self.user, 0, "CASH", "", uuid.uuid4())
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            LedgerEntry.objects.all().update(amount_cents=0)

    def test_defaults_are_independent_and_generation_idempotent(self):
        save_defaults(self.user, {self.product.pk: 2})
        self.assertEqual(DefaultItem.objects.get(user=self.user).quantity, 2)
        generate_events()
        count = Event.objects.count()
        order_count = Order.objects.count()
        balance = self.user.balance
        generate_events()
        self.assertEqual(Event.objects.count(), count)
        self.assertEqual(Order.objects.count(), order_count)
        self.assertEqual(self.user.balance, balance)
        self.assertGreaterEqual(count, 8)
        save_defaults(self.user, {self.product.pk: 1})
        self.assertEqual(self.user.balance, balance)

    def test_vacation_pauses_defaults_for_inclusive_period(self):
        save_defaults(self.user, {self.product.pk: 2})
        self.user.vacation_start = timezone.localdate() + timedelta(days=2)
        self.user.vacation_end = timezone.localdate() + timedelta(days=4)
        self.user.save()
        during = Event.objects.create(
            date=timezone.localdate() + timedelta(days=4),
            deadline=timezone.now() + timedelta(days=2),
        )
        outside = Event.objects.create(
            date=timezone.localdate() + timedelta(days=5),
            deadline=timezone.now() + timedelta(days=4),
        )

        vacation_order = provision_order(self.user, during)
        regular_order = provision_order(self.user, outside)

        self.assertEqual(vacation_order.total, 0)
        self.assertEqual(regular_order.total, 320)
        self.assertEqual(reconcile(), [])

    def test_reconciliation_detects_tampered_order(self):
        save_order(self.order.pk, {self.product.pk: 2}, self.user)
        self.order.items.update(quantity=3)
        self.assertIn(f"Order {self.order.pk}", reconcile()[0])

    def test_event_types_isolate_products_defaults_and_charges(self):
        product = Product.objects.create(
            name="Leberkassemmel (Test)", price_cents=200, event_type=EventType.LEBERKAESE
        )
        event = Event.objects.create(
            date=self.event.date,
            deadline=self.event.deadline,
            event_type=EventType.LEBERKAESE,
        )
        order = Order.objects.create(user=self.user, event=event)
        for target, wrong_product in [(self.order, product), (order, self.product)]:
            with self.assertRaises(ValidationError):
                save_order(target.pk, {wrong_product.pk: 1}, self.user)
            target.refresh_from_db()
            self.assertEqual(target.version, 0)
            self.assertFalse(target.items.exists())
        save_order(order.pk, {product.pk: 2}, self.user)
        self.assertEqual(self.user.balance, 0)
        self.assertEqual(order.charges.get().amount_cents, -400)
        save_defaults(self.user, {self.product.pk: 3, product.pk: 1})
        generate_events()
        for generated in self.user.orders.exclude(pk__in=[order.pk, self.order.pk]):
            expected_product = (
                product if generated.event.event_type == EventType.LEBERKAESE else self.product
            )
            self.assertEqual(generated.items.get().product_id, expected_product.pk)
            self.assertEqual(generated.total, 200 if expected_product == product else 480)
        self.assertEqual(reconcile(), [])

    def test_each_event_type_has_its_own_weekly_schedule(self):
        with self.settings(WEISSWURST_WEEKDAY=1, LEBERKAESE_WEEKDAY=4):
            generate_events()
        for event_type, weekday in [(EventType.WEISSWURST, 1), (EventType.LEBERKAESE, 4)]:
            events = Event.objects.filter(event_type=event_type).exclude(pk=self.event.pk)
            self.assertGreaterEqual(events.filter(date__gt=timezone.localdate()).count(), 8)
            for event in events:
                self.assertEqual(event.date.weekday(), weekday)
                deadline = timezone.localtime(event.deadline)
                self.assertEqual(deadline.date(), event.date - timedelta(days=1))
                self.assertEqual((deadline.hour, deadline.minute), (18, 0))

    def test_eight_future_events_even_on_breakfast_day(self):
        today = timezone.localdate()
        with self.settings(WEISSWURST_WEEKDAY=today.weekday(), UPCOMING_WEEKS=8):
            generate_events(today=today)
        self.assertGreaterEqual(Event.objects.filter(date__gt=today).count(), 8)
        self.assertTrue(Event.objects.filter(date=today + timedelta(weeks=8)).exists())

    def test_email_uniqueness_and_password_hash(self):
        self.assertNotEqual(self.user.password, "a-long-test-password")
        self.assertTrue(self.user.check_password("a-long-test-password"))
        self.assertNotEqual(self.user.email, self.user.paypal_email)
        with self.assertRaises((ValidationError, IntegrityError)), transaction.atomic():
            User.objects.create_user(
                "STEFAN@example.org", "test", name="Duplicate", paypal_email="another@example.org"
            )
