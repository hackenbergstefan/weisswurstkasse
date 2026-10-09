import secrets
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError

from .models import Product, User


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="E-Mail", widget=forms.EmailInput(attrs={"autocomplete": "username"})
    )
    password = forms.CharField(
        label="Passwort", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def clean(self):
        data = super().clean()
        self.user = authenticate(
            email=data.get("email", "").strip().lower(), password=data.get("password", "")
        )
        if self.user is None:
            raise ValidationError("E-Mail oder Passwort ist nicht korrekt.")
        return data


class RegisterForm(forms.ModelForm):
    password = forms.CharField(
        label="Passwort", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password_confirm = forms.CharField(
        label="Passwort wiederholen",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    invitation = forms.CharField(
        label="Einladungscode", widget=forms.PasswordInput(render_value=True)
    )

    class Meta:
        model = User
        fields = ["name", "email", "paypal_email"]
        labels = {"name": "Name", "email": "E-Mail", "paypal_email": "PayPal E-Mail"}

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_invitation(self):
        code = self.cleaned_data["invitation"]
        if not settings.INVITATION_CODE or not secrets.compare_digest(
            code.encode("utf-8"), settings.INVITATION_CODE.encode("utf-8")
        ):
            raise ValidationError("Der Einladungscode ist ungueltig.")
        return code

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Die Passwoerter stimmen nicht ueberein.")
        candidate = User(name=data.get("name", ""), email=data.get("email", ""))
        password_validation.validate_password(data.get("password", ""), candidate)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    current_password = forms.CharField(label="Aktuelles Passwort", widget=forms.PasswordInput())

    class Meta:
        model = User
        fields = ["name", "email", "paypal_email"]
        labels = {"name": "Name", "email": "E-Mail", "paypal_email": "PayPal E-Mail"}

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.instance.check_password(password):
            raise ValidationError("Das Passwort ist nicht korrekt.")
        return password


class VacationForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["vacation_start", "vacation_end"]
        labels = {"vacation_start": "Urlaub von", "vacation_end": "Urlaub bis"}
        widgets = {
            "vacation_start": forms.DateInput(attrs={"type": "date"}),
            "vacation_end": forms.DateInput(attrs={"type": "date"}),
        }


class AddOrderForm(forms.Form):
    participant = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        label="Teilnehmer",
        empty_label="Teilnehmer ausw\u00e4hlen",
    )

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        if event is not None:
            self.fields["participant"].queryset = self.fields["participant"].queryset.exclude(
                orders__event=event
            )


class PayoutForm(forms.Form):
    amount = forms.DecimalField(
        label="Betrag (EUR)",
        min_value=Decimal("0.01"),
        max_value=Decimal("10000.00"),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
    )
    note = forms.CharField(label="Notiz", required=False, max_length=500)

    @property
    def cents(self):
        return int(self.cleaned_data["amount"] * 100)


class QuantitiesForm(forms.Form):
    version = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput())

    def __init__(
        self,
        *args,
        products,
        quantities=None,
        version=None,
        show_event_type=False,
        is_free=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.products = list(products)
        self.initial["version"] = version
        for product in self.products:
            self.fields[f"product_{product.pk}"] = forms.IntegerField(
                label=(
                    f"{product.get_event_type_display()}: {product.name}"
                    if show_event_type
                    else product.name
                ),
                min_value=0,
                max_value=100,
                initial=(quantities or {}).get(product.pk, 0),
                widget=forms.NumberInput(
                    attrs={"inputmode": "numeric", "data-price": 0 if is_free else product.price_cents}
                ),
            )

    def quantities(self):
        return {product.pk: self.cleaned_data[f"product_{product.pk}"] for product in self.products}


class AmountForm(forms.Form):
    amount = forms.DecimalField(
        label="Betrag (EUR)",
        min_value=Decimal("0.01"),
        max_value=Decimal("10000.00"),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"step": "0.01", "inputmode": "decimal", "placeholder": "20.00"}
        ),
    )
    request_id = forms.UUIDField(widget=forms.HiddenInput())

    @property
    def cents(self):
        return int(self.cleaned_data["amount"] * 100)


class ManualPaymentForm(AmountForm):
    method = forms.ChoiceField(
        label="Zahlungsart",
        choices=[("CASH", "Bar"), ("BANK", "Ueberweisung"), ("OTHER", "Sonstige")],
    )
    note = forms.CharField(label="Notiz", max_length=500, required=False)


class PayPalForm(AmountForm):
    purpose = forms.ChoiceField(
        choices=[("TOPUP", "Guthaben"), ("DEBT", "Ausgleich")], widget=forms.HiddenInput()
    )
    amount = forms.DecimalField(
        label="Betrag (EUR)",
        required=False,
        min_value=Decimal("0.01"),
        max_value=Decimal("10000.00"),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
    )

    def clean(self):
        data = super().clean()
        if data.get("purpose") == "TOPUP" and not data.get("amount"):
            self.add_error("amount", "Bitte einen Betrag eingeben.")
        return data

    @property
    def cents(self):
        return int((self.cleaned_data.get("amount") or 0) * 100)


class ProductForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["event_type"].disabled = True

    class Meta:
        model = Product
        fields = ["event_type", "name", "unit", "price_cents", "active"]
        labels = {
            "event_type": "Veranstaltungstyp",
            "name": "Produkt",
            "unit": "Einheit",
            "price_cents": "Preis in Cent",
            "active": "Aktiv",
        }


class CorrectionForm(forms.Form):
    note = forms.CharField(label="Begruendung", max_length=500)
