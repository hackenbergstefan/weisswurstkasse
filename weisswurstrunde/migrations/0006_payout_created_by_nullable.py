import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0005_paypal_mail_references"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payout",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payouts",
                to="weisswurstrunde.user",
            ),
        ),
    ]
