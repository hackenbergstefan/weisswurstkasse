from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("weisswurstrunde", "0003_leberkassemmel")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_admin",
            field=models.BooleanField(default=False),
        ),
    ]
