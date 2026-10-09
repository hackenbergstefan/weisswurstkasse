import hashlib
import uuid
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlencode

import qrcode
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from qrcode.image.svg import SvgPathImage

from . import paypal, services
from .audit import record
from .forms import (
    AddOrderForm,
    CorrectionForm,
    LoginForm,
    ManualPaymentForm,
    PayoutForm,
    PayPalForm,
    ProductForm,
    ProfileForm,
    QuantitiesForm,
    RegisterForm,
    VacationForm,
)
from .models import (
    Event,
    EventType,
    LedgerEntry,
    LoginAttempt,
    Order,
    Payout,
    PayPalPayment,
    Product,
    User,
)


def error_message(error):
    return " ".join(error.messages) if isinstance(error, ValidationError) else str(error)


@transaction.atomic
def rate_limited(request, scope):
    identity = request.META.get("REMOTE_ADDR", "unknown")
    key = hashlib.sha256(f"{scope}:{identity}".encode()).hexdigest()
    attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(key=key)
    if attempt.window_start < timezone.now() - timedelta(minutes=15):
        attempt.window_start = timezone.now()
        attempt.attempts = 0
    attempt.attempts += 1
    attempt.save()
    return attempt.attempts > 20


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(request, "login"):
            return render(
                request, "weisswurstrunde/auth.html", {"form": form, "limited": True}, status=429
            )
        if form.is_valid():
            login(request, form.user)
            return redirect("dashboard")
    return render(request, "weisswurstrunde/auth.html", {"form": form})


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(
        request.POST or None,
        initial={"invitation": request.GET.get("invitation", "")}
        if request.method == "GET"
        else None,
    )
    if request.method == "POST":
        if rate_limited(request, "register"):
            return render(
                request,
                "weisswurstrunde/auth.html",
                {"form": form, "registering": True, "limited": True},
                status=429,
            )
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    for event in Event.objects.filter(status="OPEN", deadline__gt=timezone.now()):
                        services.provision_order(user, event)
                    record(
                        "user_registered",
                        user,
                        user_id=user.pk,
                        name=user.name,
                        email=user.email,
                    )
                login(request, user)
                messages.success(request, "Willkommen in der Runde.")
                return redirect("dashboard")
            except IntegrityError:
                form.add_error("email", "Diese E-Mail wird bereits verwendet.")
    return render(request, "weisswurstrunde/auth.html", {"form": form, "registering": True})


@login_required
@require_GET
def registration_qr(request):
    if not request.user.is_active or not request.user.is_admin:
        raise PermissionDenied
    if not settings.INVITATION_CODE:
        return HttpResponse("Einladungscode ist nicht konfiguriert.", status=503)

    registration_url = (
        f"{settings.PUBLIC_BASE_URL}{reverse('register')}?"
        f"{urlencode({'invitation': settings.INVITATION_CODE})}"
    )
    qr_code = qrcode.QRCode(box_size=10, border=4)
    qr_code.add_data(registration_url)
    qr_code.make(fit=True)
    image = qr_code.make_image(image_factory=SvgPathImage)
    output = BytesIO()
    image.save(output)

    response = HttpResponse(output.getvalue(), content_type="image/svg+xml")
    response["Content-Disposition"] = 'inline; filename="einladung.svg"'
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def order_context(order, actor):
    can_edit = order.event.can_edit(actor)
    products = list(Product.objects.filter(active=True, event_type=order.event.event_type))
    quantities = {item.product_id: item.quantity for item in order.items.all()}
    form = QuantitiesForm(
        products=products, quantities=quantities, version=order.version, prefix=f"order-{order.pk}"
    )
    rows = [{"product": product, "field": form[f"product_{product.pk}"]} for product in products]
    inactive = [item for item in order.items.all() if not item.product.active]
    fixed_total = sum(item.quantity * item.unit_price_cents for item in inactive)
    editable_total = fixed_total + sum(
        product.price_cents * quantities.get(product.pk, 0) for product in products
    )
    return {
        "order": order,
        "form": form,
        "rows": rows,
        "inactive": inactive,
        "fixed_total": fixed_total,
        "can_edit": can_edit,
        "display_total": editable_total if can_edit else order.total,
    }


@login_required
@require_GET
def dashboard(request):
    orders = (
        request.user.orders.filter(event__deadline__gt=timezone.now())
        .select_related("event")
        .prefetch_related("items__product")
        .order_by("event__date", "event__event_type")
    )
    upcoming = [order_context(order, request.user) for order in orders]
    return render(
        request,
        "weisswurstrunde/dashboard.html",
        {"upcoming": upcoming, "balance": request.user.balance, "today": timezone.localdate()},
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_order(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("event", "user").prefetch_related("items__product"),
        pk=order_id,
    )
    context = order_context(order, request.user)
    if request.method == "POST":
        form = QuantitiesForm(
            request.POST,
            products=Product.objects.filter(active=True, event_type=order.event.event_type),
            prefix=f"order-{order.pk}",
        )
        if form.is_valid() and form.cleaned_data["version"] is not None:
            try:
                services.save_order(
                    order.pk, form.quantities(), request.user, form.cleaned_data["version"]
                )
                messages.success(
                    request,
                    f"Sauber! Bestellung ist raus. {order.user.name}, {order.event.date:%d.%m.}",
                )
                if request.user.is_admin:
                    return redirect(f"{reverse('orders')}?event={order.event_id}")
                return redirect("dashboard" if order.user_id == request.user.pk else "orders")
            except ValidationError as error:
                form.add_error(None, error)
        else:
            form.add_error(None, "Bitte Eingaben pruefen und die Seite bei Bedarf neu laden.")
        context["form"] = form
        context["rows"] = [
            {"product": product, "field": form[f"product_{product.pk}"]}
            for product in form.products
        ]
    return render(request, "weisswurstrunde/edit_order.html", context)


@login_required
@require_GET
def orders(request):
    event_type = request.GET.get("event_type", "")
    event_types = EventType.choices
    if event_type not in dict(event_types):
        event_type = ""
    events = Event.objects.order_by("-date", "event_type")
    if event_type:
        events = events.filter(event_type=event_type)
    count_options = (10, 20, 50, 100)
    try:
        count = int(request.GET.get("count", "10"))
    except (ValueError, TypeError):
        count = 10
    if count not in count_options:
        count = 10
    selected = request.GET.get("event", "")
    event = None
    if selected:
        try:
            event = events.filter(pk=int(selected)).first()
        except (ValueError, OverflowError):
            pass
        if event is None:
            messages.error(request, "Dieser Termin existiert nicht.")
    if event is None:
        upcoming = list(
            events.filter(date__gte=timezone.localdate()).order_by("date", "event_type")[:count]
        )
        quantities = list(
            Order.objects.filter(event__in=upcoming)
            .order_by()
            .values("event_id", "items__product_id")
            .annotate(quantity=Sum("items__quantity"))
        )
        products = list(
            Product.objects.filter(
                Q(active=True, event_type__in={event.event_type for event in upcoming})
                | Q(
                    pk__in=[
                        row["items__product_id"] for row in quantities if row["items__product_id"]
                    ]
                )
            )
        )
        totals_by_product = {
            (row["event_id"], row["items__product_id"]): row["quantity"] or 0 for row in quantities
        }
        summaries = [
            {
                "event": upcoming_event,
                "quantities": [
                    totals_by_product.get((upcoming_event.pk, product.pk), 0)
                    if product.event_type == upcoming_event.event_type
                    else None
                    for product in products
                ],
            }
            for upcoming_event in upcoming
        ]
        return render(
            request,
            "weisswurstrunde/orders.html",
            {
                "events": events,
                "event": None,
                "count": count,
                "count_options": count_options,
                "summaries": summaries,
                "products": products,
                "summary_columns": len(products) + 2
                if request.user.is_admin
                else len(products) + 1,
                "event_type": event_type,
                "event_types": event_types,
            },
        )
    event_orders = (
        Order.objects.filter(event=event)
        .select_related("user", "event")
        .prefetch_related("items__product")
        .order_by("user__name")
        if event
        else []
    )
    totals = {}
    grand_total = 0
    participant_count = 0
    for order in event_orders:
        items = order.items.all()
        if items:
            participant_count += 1
        grand_total += order.total
        for item in items:
            totals[item.product.name] = totals.get(item.product.name, 0) + item.quantity
    return render(
        request,
        "weisswurstrunde/orders.html",
        {
            "events": events,
            "event": event,
            "orders": event_orders,
            "totals": totals,
            "grand_total": grand_total,
            "participant_count": participant_count,
            "can_edit": event.can_edit(request.user),
            "add_order_form": (
                AddOrderForm(event=event)
                if request.user.is_admin and event.status != Event.Status.CANCELLED
                else None
            ),
            "count": count,
            "count_options": count_options,
            "event_type": event_type,
            "event_types": event_types,
        },
    )


@login_required
@require_GET
def order_history(request):
    past_events = Event.objects.filter(date__lt=timezone.localdate()).order_by(
        "-date", "-event_type"
    )
    page = Paginator(past_events, 50).get_page(request.GET.get("page"))
    items_by_event = {}
    for item in (
        Order.objects.filter(event__in=page.object_list)
        .values("event_id", "items__product__name", "items__product__unit")
        .annotate(
            quantity=Sum("items__quantity"),
            amount=Sum(F("items__quantity") * F("items__unit_price_cents")),
        )
        .order_by("event_id", "items__product__name")
    ):
        items_by_event.setdefault(item["event_id"], []).append(item)

    summaries = []
    for event in page:
        items = items_by_event.get(event.pk, [])
        summaries.append(
            {
                "event": event,
                "items": items,
                "total": sum(item["amount"] or 0 for item in items),
            }
        )
    return render(
        request, "weisswurstrunde/order_history.html", {"events": page, "summaries": summaries}
    )


@login_required
@require_POST
def cancel_event(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    try:
        services.cancel_event(event.pk, request.user)
    except ValidationError as error:
        messages.error(request, error_message(error))
    else:
        messages.success(request, "Termin abgesagt.")
    return redirect(f"{reverse('orders')}?event={event.pk}")


@login_required
@require_POST
def add_order(request, event_id):
    if not request.user.is_active or not request.user.is_admin:
        raise PermissionDenied
    event = get_object_or_404(Event, pk=event_id)
    form = AddOrderForm(request.POST)
    if form.is_valid():
        try:
            order = services.add_order(form.cleaned_data["participant"], event, request.user)
        except ValidationError as error:
            messages.error(request, error_message(error))
        else:
            return redirect("edit_order", order_id=order.pk)
    else:
        messages.error(request, "Bitte einen aktiven Teilnehmer auswaehlen.")
    return redirect(f"{reverse('orders')}?event={event.pk}")


@login_required
@require_GET
def participants(request):
    users = User.objects.annotate(
        current_balance=Coalesce(
            Sum("ledger__amount_cents", filter=LedgerEntry.balance_filter("ledger__")), Value(0)
        )
    )
    return render(request, "weisswurstrunde/participants.html", {"participants": users})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    profile_form = ProfileForm(instance=request.user, prefix="profile")
    password_form = PasswordChangeForm(request.user, prefix="password")
    vacation_form = VacationForm(instance=request.user, prefix="vacation")
    products = Product.objects.filter(active=True)
    defaults_form = QuantitiesForm(
        products=products,
        quantities=dict(request.user.default_items.values_list("product_id", "quantity")),
        prefix="defaults",
        show_event_type=True,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            profile_form = ProfileForm(request.POST, instance=request.user, prefix="profile")
            if profile_form.is_valid():
                try:
                    with transaction.atomic():
                        profile_form.save()
                    record("profile_edited", request.user, user_id=request.user.pk)
                    messages.success(request, "Profil gespeichert.")
                    return redirect("profile")
                except IntegrityError:
                    profile_form.add_error("email", "Diese E-Mail wird bereits verwendet.")
        elif action == "password":
            password_form = PasswordChangeForm(request.user, request.POST, prefix="password")
            if password_form.is_valid():
                user = password_form.save()
                record("password_changed", request.user, user_id=request.user.pk)
                update_session_auth_hash(request, user)
                messages.success(request, "Passwort geaendert.")
                return redirect("profile")
        elif action == "vacation":
            vacation_form = VacationForm(request.POST, instance=request.user, prefix="vacation")
            if vacation_form.is_valid():
                vacation_form.save()
                record(
                    "vacation_edited",
                    request.user,
                    user_id=request.user.pk,
                    vacation_start=request.user.vacation_start,
                    vacation_end=request.user.vacation_end,
                )
                messages.success(request, "Urlaubszeitraum gespeichert.")
                return redirect("profile")
        elif action == "defaults":
            defaults_form = QuantitiesForm(
                request.POST, products=products, prefix="defaults", show_event_type=True
            )
            if defaults_form.is_valid():
                services.save_defaults(request.user, defaults_form.quantities())
                messages.success(
                    request,
                    "Standardbestellung gespeichert. Bestehende Bestellungen bleiben unveraendert.",
                )
                return redirect("profile")
    return render(
        request,
        "weisswurstrunde/profile.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
            "vacation_form": vacation_form,
            "defaults_form": defaults_form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def products(request):
    if not request.user.is_admin:
        raise PermissionDenied
    product = None
    if request.method == "POST" and request.POST.get("product_id"):
        identifier = forms.IntegerField(min_value=1)
        try:
            product_id = identifier.clean(request.POST["product_id"])
        except ValidationError:
            return HttpResponse(status=400)
        product = get_object_or_404(Product, pk=product_id)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                saved_product = form.save()
            record(
                "product_edited",
                request.user,
                product_id=saved_product.pk,
                name=saved_product.name,
                event_type=saved_product.event_type,
                price_cents=saved_product.price_cents,
                active=saved_product.active,
            )
            messages.success(request, "Produkt gespeichert. Bestehende Preise bleiben erhalten.")
            return redirect("products")
        except IntegrityError:
            form.add_error("name", "Dieses Produkt existiert bereits.")
    return render(
        request,
        "weisswurstrunde/products.html",
        {
            "product_forms": [
                (item, ProductForm(instance=item, prefix=str(item.pk)))
                for item in Product.objects.all()
            ],
            "form": form,
            "editing": product,
        },
    )


def cashbox_summary():
    balances = [user.balance for user in User.objects.filter(is_active=True)]
    payouts = (
        Payout.objects.filter(status=Payout.Status.COMPLETED).aggregate(total=Sum("amount_cents"))[
            "total"
        ]
        or 0
    )
    credit = sum(balance for balance in balances if balance > 0)
    outstanding = sum(-balance for balance in balances if balance < 0)
    return {
        "credit": credit,
        "outstanding": outstanding,
        "payouts": payouts,
        "total": credit - outstanding - payouts,
    }


@login_required
@require_GET
def finance_overview(request):
    if not request.user.is_active or not request.user.is_admin:
        raise PermissionDenied

    entries = (
        LedgerEntry.objects.filter(LedgerEntry.balance_filter())
        .select_related("user", "recorded_by", "order__event")
        .order_by("-created_at", "-pk")
    )
    entry_totals = entries.aggregate(
        income=Sum("amount_cents", filter=Q(amount_cents__gt=0)),
        expenses=Sum("amount_cents", filter=Q(amount_cents__lt=0)),
    )
    payouts = Payout.objects.select_related("created_by").order_by("-created_at", "-pk")
    payout_total = (
        payouts.filter(status=Payout.Status.COMPLETED).aggregate(total=Sum("amount_cents"))["total"]
        or 0
    )
    pending_payments = (
        PayPalPayment.objects.filter(status__in=paypal.OPEN_PAYMENT_STATUSES)
        .select_related("user")
        .order_by("-created_at", "-pk")
    )
    return render(
        request,
        "weisswurstrunde/finance_overview.html",
        {
            "entries": Paginator(entries, 50).get_page(request.GET.get("entries_page")),
            "payouts": Paginator(payouts, 50).get_page(request.GET.get("payouts_page")),
            "pending_payments": Paginator(pending_payments, 50).get_page(
                request.GET.get("paypal_page")
            ),
            "income_total": entry_totals["income"] or 0,
            "expense_total": abs(entry_totals["expenses"] or 0),
            "payout_total": payout_total,
            "cashbox_total": cashbox_summary()["total"],
            "pending_paypal_total": pending_payments.aggregate(total=Sum("amount_cents"))["total"]
            or 0,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def payments(request):
    if request.user.is_admin:
        payout_form = PayoutForm()
        if request.method == "POST" and request.POST.get("action") == "payout":
            payout_form = PayoutForm(request.POST)
            if payout_form.is_valid():
                try:
                    services.create_payout(
                        payout_form.cents,
                        Payout.Method.CASH,
                        "",
                        payout_form.cleaned_data["note"],
                        request.user,
                    )
                    messages.success(request, "Bargeldauszahlung angelegt.")
                    return redirect("payments")
                except (paypal.PayPalError, ValidationError) as error:
                    payout_form.add_error(None, error_message(error))
    else:
        payout_form = None
    manual_form = ManualPaymentForm(initial={"request_id": uuid.uuid4()}, prefix="manual")
    paypal_form = PayPalForm(
        initial={"request_id": uuid.uuid4(), "purpose": "TOPUP", "amount": "20.00"}, prefix="paypal"
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "manual":
            manual_form = ManualPaymentForm(request.POST, prefix="manual")
            if manual_form.is_valid():
                try:
                    services.manual_payment(
                        request.user,
                        manual_form.cents,
                        manual_form.cleaned_data["method"],
                        manual_form.cleaned_data["note"],
                        manual_form.cleaned_data["request_id"],
                    )
                    messages.success(request, "Zahlung verbucht.")
                    return redirect("payments")
                except ValidationError as error:
                    manual_form.add_error(None, error)
        elif action == "paypal":
            paypal_form = PayPalForm(request.POST, prefix="paypal")
            if paypal_form.is_valid():
                try:
                    payment = paypal.create_payment(
                        request.user,
                        paypal_form.cents,
                        paypal_form.cleaned_data["purpose"],
                        paypal_form.cleaned_data["request_id"],
                    )
                    request.session["paypal_payment"] = str(payment.pk)
                    return redirect(payment.approval_url)
                except (paypal.PayPalError, ValidationError) as error:
                    paypal_form.add_error(None, error_message(error))
    return render(
        request,
        "weisswurstrunde/payments.html",
        {
            "balance": request.user.balance,
            "manual_form": manual_form,
            "paypal_form": paypal_form,
            "paypal_enabled": paypal.enabled(),
            "debt_token": uuid.uuid4(),
            "payment_records": request.user.paypal_payments.order_by("-created_at")[:30],
            "payout_form": payout_form,
            "payout_records": Payout.objects.select_related("created_by").order_by("-created_at")[
                :50
            ]
            if request.user.is_admin
            else [],
            "cashbox": cashbox_summary() if request.user.is_admin else None,
        },
    )


@login_required
@require_POST
def paypal_sync(request, payment_id):
    payment = get_object_or_404(PayPalPayment, pk=payment_id, user=request.user)
    try:
        payment = paypal.sync_payment(payment.pk)
        messages.success(request, f"PayPal-Mailabgleich: {payment.get_status_display()}.")
    except (paypal.PayPalError, ValidationError) as error:
        messages.error(request, error_message(error))
    return redirect("payments")


@login_required
@require_POST
@transaction.atomic
def paypal_delete(request, payment_id):
    payment = get_object_or_404(
        PayPalPayment.objects.select_for_update(),
        pk=payment_id,
        user=request.user,
        status__in=[PayPalPayment.Status.CREATED, PayPalPayment.Status.PENDING],
    )
    payment.delete()
    record("paypal_payment_deleted", request.user, payment_id=payment.pk)
    messages.success(request, "Ausstehende PayPal-Zahlung entfernt.")
    return redirect("payments")


@login_required
@require_GET
def history(request, user_id=None):
    user = get_object_or_404(User, pk=user_id) if user_id else request.user
    entries = list(
        user.ledger.filter(LedgerEntry.balance_filter()).select_related("recorded_by", "reversal")
    )
    running = 0
    for entry in entries:
        running += entry.amount_cents
        entry.running_balance = running
        entry.display_note = (
            entry.note.split(" (Stand ", 1)[0]
            if entry.kind == LedgerEntry.Kind.ORDER
            else entry.note
        )
    page = Paginator(list(reversed(entries)), 40).get_page(request.GET.get("page"))
    return render(
        request,
        "weisswurstrunde/history.html",
        {"account": user, "entries": page, "balance": running},
    )


@login_required
@require_http_methods(["GET", "POST"])
def correction(request, entry_id):
    entry = get_object_or_404(LedgerEntry, pk=entry_id, kind="MANUAL")
    form = CorrectionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.reverse_manual(entry.pk, request.user, form.cleaned_data["note"])
        messages.success(
            request, "Gegenbuchung erstellt. Die urspruengliche Zahlung bleibt erhalten."
        )
        return redirect("user_history", user_id=entry.user_id)
    return render(request, "weisswurstrunde/correction.html", {"entry": entry, "form": form})
