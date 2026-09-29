from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0004_payout"),
    ]

    operations = [
        migrations.AddField(
            model_name="paypalpayment",
            name="provider_message_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="paypalpayment",
            name="provider_reference",
            field=models.CharField(blank=True, max_length=180, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="payout",
            name="provider_message_id",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
    ]
