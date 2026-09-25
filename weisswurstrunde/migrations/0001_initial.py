import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='LoginAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=64, unique=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('window_start', models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('name', models.CharField(max_length=120)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('paypal_email', models.EmailField(max_length=254)),
                ('is_active', models.BooleanField(default=True)),
                ('is_admin', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'ordering': ['name', 'pk'],
                'constraints': [models.UniqueConstraint(django.db.models.functions.text.Lower('email'), name='unique_login_email_ci')],
            },
        ),
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('WEISSWURST', 'Weißwurst'), ('LEBERKAESE', 'Leberkäse')], default='WEISSWURST', max_length=12)),
                ('date', models.DateField()),
                ('deadline', models.DateTimeField()),
                ('status', models.CharField(choices=[('OPEN', 'Offen'), ('LOCKED', 'Bestellschluss'), ('SETTLED', 'Abgeschlossen')], default='OPEN', max_length=10)),
            ],
            options={
                'ordering': ['date', 'event_type'],
                'constraints': [models.UniqueConstraint(fields=('date', 'event_type'), name='one_event_per_type_date')],
            },
        ),
        migrations.CreateModel(
            name='Order',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='orders', to='weisswurstrunde.event')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='orders', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='PayPalPayment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount_cents', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ('purpose', models.CharField(choices=[('TOPUP', 'Guthaben'), ('DEBT', 'Ausgleich')], max_length=10)),
                ('status', models.CharField(choices=[('CREATED', 'Erstellt'), ('APPROVED', 'Freigegeben'), ('PENDING', 'Ausstehend'), ('COMPLETED', 'Erfolgreich'), ('CANCELLED', 'Abgebrochen'), ('FAILED', 'Fehlgeschlagen'), ('REFUNDED', 'Erstattet'), ('PARTIALLY_REFUNDED', 'Teilweise erstattet')], default='CREATED', max_length=24)),
                ('order_id', models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ('capture_id', models.CharField(blank=True, max_length=100, null=True, unique=True)),
                ('refunded_cents', models.PositiveIntegerField(default=0)),
                ('approval_url', models.URLField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='paypal_payments', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='LedgerEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount_cents', models.IntegerField()),
                ('kind', models.CharField(choices=[('ORDER', 'Bestellung'), ('MANUAL', 'Manuelle Zahlung'), ('PAYPAL', 'PayPal'), ('REFUND', 'Erstattung'), ('CORRECTION', 'Korrektur')], max_length=12)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('status', models.CharField(choices=[('POSTED', 'Gebucht')], default='POSTED', max_length=10)),
                ('method', models.CharField(blank=True, max_length=20)),
                ('reference', models.CharField(max_length=180, unique=True)),
                ('note', models.CharField(blank=True, max_length=500)),
                ('recorded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='recorded_entries', to=settings.AUTH_USER_MODEL)),
                ('reverses', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reversal', to='weisswurstrunde.ledgerentry')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ledger', to=settings.AUTH_USER_MODEL)),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='charges', to='weisswurstrunde.order')),
                ('payment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='entries', to='weisswurstrunde.paypalpayment')),
            ],
            options={
                'ordering': ['created_at', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('WEISSWURST', 'Weißwurst'), ('LEBERKAESE', 'Leberkäse')], default='WEISSWURST', max_length=12)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('unit', models.CharField(default='Stueck', max_length=30)),
                ('price_cents', models.PositiveIntegerField(validators=[django.core.validators.MaxValueValidator(1000000)])),
                ('active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['pk'],
                'constraints': [models.CheckConstraint(condition=models.Q(('price_cents__gte', 0)), name='positive_price')],
            },
        ),
        migrations.CreateModel(
            name='OrderItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(validators=[django.core.validators.MaxValueValidator(100)])),
                ('unit_price_cents', models.PositiveIntegerField()),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='weisswurstrunde.order')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='weisswurstrunde.product')),
            ],
        ),
        migrations.CreateModel(
            name='DefaultItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(validators=[django.core.validators.MaxValueValidator(100)])),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='default_items', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='weisswurstrunde.product')),
            ],
        ),
        migrations.AddConstraint(
            model_name='order',
            constraint=models.UniqueConstraint(fields=('user', 'event'), name='one_order_per_event'),
        ),
        migrations.AddConstraint(
            model_name='ledgerentry',
            constraint=models.CheckConstraint(condition=models.Q(('amount_cents', 0), _negated=True), name='nonzero_ledger_entry'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.UniqueConstraint(fields=('order', 'product'), name='one_product_per_order'),
        ),
        migrations.AddConstraint(
            model_name='orderitem',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gte', 0), ('quantity__lte', 100)), name='order_quantity_range'),
        ),
        migrations.AddConstraint(
            model_name='defaultitem',
            constraint=models.UniqueConstraint(fields=('user', 'product'), name='one_default_per_product'),
        ),
        migrations.AddConstraint(
            model_name='defaultitem',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gte', 0), ('quantity__lte', 100)), name='default_quantity_range'),
        ),
    ]
