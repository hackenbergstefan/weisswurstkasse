import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    paypal_email = models.EmailField()
    vacation_start = models.DateField(null=True, blank=True)
    vacation_end = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name", "paypal_email"]

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_login_email_ci")]

    def clean(self):
        super().clean()
        self.email = self.email.strip().lower()
        self.paypal_email = self.paypal_email.strip().lower()
        if (self.vacation_start is None) != (self.vacation_end is None):
            raise ValidationError(
                "Urlaubsbeginn und Urlaubsende muessen gemeinsam angegeben werden."
            )
        if self.vacation_start and self.vacation_end and self.vacation_start > self.vacation_end:
            raise ValidationError("Das Urlaubsende darf nicht vor dem Urlaubsbeginn liegen.")

    def is_on_vacation(self, event_date):
        return (
            self.vacation_start is not None
            and self.vacation_end is not None
            and self.vacation_start <= event_date <= self.vacation_end
        )

    @property
    def balance(self):
        return (
            self.ledger.filter(LedgerEntry.balance_filter()).aggregate(total=Sum("amount_cents"))[
                "total"
            ]
            or 0
        )

    def __str__(self):
        return self.name


class EventType(models.TextChoices):
    WEISSWURST = "WEISSWURST", "Wei\u00dfwurst"
    LEBERKAESE = "LEBERKAESE", "Leberk\u00e4se"


class Product(models.Model):
    event_type = models.CharField(max_length=12, choices=EventType, default=EventType.WEISSWURST)
    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=30, default="Stueck")
    price_cents = models.PositiveIntegerField(validators=[MaxValueValidator(1000000)])
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(price_cents__gte=0), name="positive_price")
        ]

    def __str__(self):
        return self.name


class Event(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Offen"
        LOCKED = "LOCKED", "Bestellschluss"
        SETTLED = "SETTLED", "Abgeschlossen"
        CANCELLED = "CANCELLED", "Abgesagt"

    event_type = models.CharField(max_length=12, choices=EventType, default=EventType.WEISSWURST)
    date = models.DateField()
    deadline = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status, default=Status.OPEN)

    class Meta:
        ordering = ["date", "event_type"]
        constraints = [
            models.UniqueConstraint(fields=["date", "event_type"], name="one_event_per_type_date")
        ]

    @property
    def editable(self):
        return self.status == self.Status.OPEN and timezone.now() < self.deadline

    def can_edit(self, user):
        return (
            self.status != self.Status.CANCELLED
            and user.is_active
            and (self.editable or user.is_admin)
        )


class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="orders")
    version = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "event"], name="one_order_per_event")
        ]

    @property
    def total(self):
        return sum(item.quantity * item.unit_price_cents for item in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MaxValueValidator(100)])
    unit_price_cents = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="one_product_per_order"),
            models.CheckConstraint(
                condition=Q(quantity__gte=0, quantity__lte=100), name="order_quantity_range"
            ),
        ]


class DefaultItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="default_items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(validators=[MaxValueValidator(100)])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "product"], name="one_default_per_product"),
            models.CheckConstraint(
                condition=Q(quantity__gte=0, quantity__lte=100), name="default_quantity_range"
            ),
        ]


class PayPalPayment(models.Model):
    class Status(models.TextChoices):
        CREATED = "CREATED", "Erstellt"
        APPROVED = "APPROVED", "Freigegeben"
        PENDING = "PENDING", "Ausstehend"
        COMPLETED = "COMPLETED", "Erfolgreich"
        CANCELLED = "CANCELLED", "Abgebrochen"
        FAILED = "FAILED", "Fehlgeschlagen"
        REFUNDED = "REFUNDED", "Erstattet"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Teilweise erstattet"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="paypal_payments")
    amount_cents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    matching_cents = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(30)]
    )
    purpose = models.CharField(
        max_length=10, choices=[("TOPUP", "Guthaben"), ("DEBT", "Ausgleich")]
    )
    status = models.CharField(max_length=24, choices=Status, default=Status.CREATED)
    order_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    capture_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    refunded_cents = models.PositiveIntegerField(default=0)
    approval_url = models.URLField(blank=True)
    provider_reference = models.CharField(max_length=180, unique=True, null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["matching_cents"],
                condition=Q(status__in=["CREATED", "APPROVED", "PENDING"]),
                name="unique_open_paypal_matching_cents",
            )
        ]


class Payout(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "Bargeld"
        PAYPAL = "PAYPAL", "PayPal"

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Abgeschlossen"
        PENDING = "PENDING", "Ausstehend"
        FAILED = "FAILED", "Fehlgeschlagen"

    amount_cents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    method = models.CharField(max_length=8, choices=Method)
    status = models.CharField(max_length=9, choices=Status, default=Status.COMPLETED)
    recipient = models.EmailField(blank=True)
    note = models.CharField(max_length=500, blank=True)
    provider_batch_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="payouts", null=True, blank=True
    )
    created_at = models.DateTimeField(default=timezone.now)


class ImmutableLedgerQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Ledger entries are immutable; record a correction.")

    def delete(self):
        raise ValidationError("Ledger entries cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Ledger entries are immutable; record a correction.")


class LedgerEntry(models.Model):
    class Kind(models.TextChoices):
        ORDER = "ORDER", "Bestellung"
        MANUAL = "MANUAL", "Manuelle Zahlung"
        PAYPAL = "PAYPAL", "PayPal"
        REFUND = "REFUND", "Erstattung"
        CORRECTION = "CORRECTION", "Korrektur"

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="ledger")
    amount_cents = models.IntegerField()
    kind = models.CharField(max_length=12, choices=Kind)
    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, default="POSTED", choices=[("POSTED", "Gebucht")])
    method = models.CharField(max_length=20, blank=True)
    reference = models.CharField(max_length=180, unique=True)
    note = models.CharField(max_length=500, blank=True)
    order = models.ForeignKey(
        Order, on_delete=models.PROTECT, null=True, blank=True, related_name="charges"
    )
    payment = models.ForeignKey(
        PayPalPayment, on_delete=models.PROTECT, null=True, blank=True, related_name="entries"
    )
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal"
    )
    recorded_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="recorded_entries", null=True
    )
    objects = ImmutableLedgerQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(amount_cents=0), name="nonzero_ledger_entry")
        ]

    @staticmethod
    def balance_filter(prefix=""):
        today = timezone.localdate()
        return Q(**{f"{prefix}status": "POSTED", f"{prefix}created_at__date__lte": today}) & (
            Q(**{f"{prefix}order__isnull": True}) | Q(**{f"{prefix}order__event__date__lte": today})
        )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Ledger entries are immutable; record a correction.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted.")


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField(default=timezone.now)
