from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("weisswurstrunde", "0002_user_vacation_end_user_vacation_start"),
    ]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="status",
            field=models.CharField(
                choices=[
                    ("OPEN", "Offen"),
                    ("LOCKED", "Bestellschluss"),
                    ("SETTLED", "Abgeschlossen"),
                    ("CANCELLED", "Abgesagt"),
                ],
                default="OPEN",
                max_length=10,
            ),
        ),
    ]
