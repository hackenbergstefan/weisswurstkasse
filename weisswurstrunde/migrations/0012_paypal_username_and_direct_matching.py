import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


def remove_pending_paypal_payments(apps, schema_editor):
    PayPalPayment = apps.get_model("weisswurstrunde", "PayPalPayment")
    PayPalPayment.objects.exclude(
        status__in=["COMPLETED", "REFUNDED", "PARTIALLY_REFUNDED", "UNMATCHED"]
    ).filter(entries__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0011_consolidate_order_charges"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="paypalpayment",
            name="unique_open_paypal_matching_cents",
        ),
        migrations.RemoveField(
            model_name="paypalpayment",
            name="matching_cents",
        ),
        migrations.RemoveField(
            model_name="user",
            name="paypal_email",
        ),
        migrations.AddField(
            model_name="user",
            name="paypal_username",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="user",
            name="paypal_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("paypal_username"),
                condition=~Q(paypal_username=""),
                name="unique_paypal_username_ci",
            ),
        ),
        migrations.AddField(
            model_name="paypalpayment",
            name="sender_name",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="paypalpayment",
            name="subject",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="paypalpayment",
            name="purpose",
            field=models.CharField(
                choices=[("TOPUP", "Guthaben"), ("DEBT", "Ausgleich")],
                default="TOPUP",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="paypalpayment",
            name="status",
            field=models.CharField(
                choices=[
                    ("UNMATCHED", "Nicht zugeordnet"),
                    ("COMPLETED", "Erfolgreich"),
                    ("REFUNDED", "Erstattet"),
                    ("PARTIALLY_REFUNDED", "Teilweise erstattet"),
                ],
                default="COMPLETED",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="paypalpayment",
            name="user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="paypal_payments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(remove_pending_paypal_payments, migrations.RunPython.noop),
    ]
