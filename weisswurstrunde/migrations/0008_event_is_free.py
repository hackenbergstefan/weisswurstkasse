from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("weisswurstrunde", "0007_paypalpayment_matching_cents")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="is_free",
            field=models.BooleanField(default=False),
        ),
    ]