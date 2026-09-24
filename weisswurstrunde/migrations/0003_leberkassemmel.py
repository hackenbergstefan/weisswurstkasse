from django.db import migrations


def add_leberkassemmel(apps, schema_editor):
    product = apps.get_model("weisswurstrunde", "Product")
    product.objects.using(schema_editor.connection.alias).get_or_create(
        name="Leberkassemmel",
        defaults={
            "event_type": "LEBERKAESE",
            "unit": "St\u00fcck",
            "price_cents": 200,
            "active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("weisswurstrunde", "0002_event_types")]
    operations = [migrations.RunPython(add_leberkassemmel, migrations.RunPython.noop)]
