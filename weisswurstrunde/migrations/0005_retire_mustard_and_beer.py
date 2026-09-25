from django.db import migrations


def retire_products(apps, schema_editor):
    product = apps.get_model("weisswurstrunde", "Product")
    product.objects.using(schema_editor.connection.alias).filter(
        name__in=["S\u00fc\u00dfer Senf", "Wei\u00dfbier"],
    ).update(active=False)


class Migration(migrations.Migration):
    dependencies = [("weisswurstrunde", "0004_user_is_admin")]

    operations = [migrations.RunPython(retire_products, migrations.RunPython.noop)]
