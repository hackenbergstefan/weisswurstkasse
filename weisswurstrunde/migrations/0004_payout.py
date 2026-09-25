import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("weisswurstrunde", "0003_event_cancelled")]

    operations = [
        migrations.CreateModel(
            name="Payout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount_cents", models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ("method", models.CharField(choices=[("CASH", "Bargeld"), ("PAYPAL", "PayPal")], max_length=8)),
                ("status", models.CharField(choices=[("COMPLETED", "Abgeschlossen"), ("PENDING", "Ausstehend"), ("FAILED", "Fehlgeschlagen")], default="COMPLETED", max_length=9)),
                ("recipient", models.EmailField(blank=True, max_length=254)),
                ("note", models.CharField(blank=True, max_length=500)),
                ("provider_batch_id", models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payouts", to="weisswurstrunde.user")),
            ],
        ),
    ]
