from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0006_payout_created_by_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="paypalpayment",
            name="matching_cents",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[MinValueValidator(0), MaxValueValidator(30)],
            ),
        ),
        migrations.AddConstraint(
            model_name="paypalpayment",
            constraint=models.UniqueConstraint(
                condition=Q(("status__in", ["CREATED", "APPROVED", "PENDING"])),
                fields=("matching_cents",),
                name="unique_open_paypal_matching_cents",
            ),
        ),
    ]
