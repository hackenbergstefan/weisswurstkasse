# Weisswurstrunde

A self-hosted, mobile-first breakfast order book and shared cash ledger.
Django 5.2 LTS, Python 3.12+, uv, SQLite, server-rendered templates and small,
progressive JavaScript enhancements. No frontend build or external CDN is required.

The Django app and database tables use the `weisswurstrunde` name. Databases from
the former `breakfast` app must be recreated; existing records are not migrated.

## Local development

Install [uv](https://docs.astral.sh/uv/). In a **fish** shell:

```fish
uv sync --frozen
set -gx SECRET_KEY (openssl rand -hex 32)
set -gx INVITATION_CODE (openssl rand -hex 16)
set -gx DEBUG true
uv run python manage.py migrate
uv run python manage.py seed_products
uv run python manage.py generate_events
uv run python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/register/ and use the invitation code from your local
environment. Keep the environment in your terminal while running management
commands. `seed_products` is optional and adds **example prices**; review them in
**Sortiment** before real use. There are no seeded accounts or default passwords.

Run `uv run python manage.py worker` in another terminal with the same environment
for automatic event generation and PayPal reconciliation. The worker runs every
15 minutes; `worker --once` performs one cycle.

## Design

The application uses a single **Die Weisswurstmaschine** design: blue and yellow,
Bangers display type, a local Augsburg illustration, product cards and a live
order receipt. There is no theme selector or browser-stored design preference.
The reference is `design-previews/weisswurstmaschine.html`; its illustration
source remains in `design-previews/stammtisch-art.js`. Production uses exported
PNG assets, so the artwork also works without JavaScript and under the existing
Content Security Policy. Fonts, images and license details are in `static/ASSETS.md`.

All active products for the current event are shown directly, without category
filters. The initial assortment contains Weisswurst, Breze and Leberkassemmel;
the former mustard and beer products are inactive, preserving existing orders
and ledger entries. Without JavaScript, quantity inputs and normal form
submissions remain available; the server is authoritative for prices and permissions.
Run `node --test tests/orders.test.cjs` for receipt and quantity-control tests.

## Docker deployment

1. Create an untracked `.env` using `deployment.example` as the reference. Fill in
   a randomly generated `SECRET_KEY` (at least 32 random bytes), invitation code,
   domain, public HTTPS URL and trusted CSRF origin. Never reuse example/test keys.
2. Keep `DEBUG=false`. Put a TLS reverse proxy in front of the loopback-bound
   container port. Set `ALLOWED_HOSTS` to your domain plus `localhost,127.0.0.1`.
3. Set `TRUST_PROXY=true` **only** when the proxy overwrites
   `X-Forwarded-Proto` and clients cannot connect directly to the app.
4. Start the application:

```fish
docker compose up --build -d
docker compose exec web /app/.venv/bin/python manage.py seed_products
```

The web container runs migrations before accepting traffic. The worker starts
after the web health check succeeds. Both use the named `breakfast-data` volume
mounted at `/data`. The image runs as UID/GID 10001, not root.
Do not remove the volume on upgrades. Start only one scheduler per installation.
The health endpoint is intentionally public and returns only `ok`.

For tests against plain HTTP on a trusted development machine, use `DEBUG=true`;
production otherwise requires HTTPS and secure session/CSRF cookies. The reverse
proxy should limit request sizes and apply an authentication rate limit. An
application-level limit also caps login/registration attempts per source IP.
Behind a reverse proxy this conservatively limits all users sharing its address.

Use a stable secret key across deployments. A changed key invalidates sessions.
Registration fails closed when the invitation code is empty.
HSTS preload enrollment is intentionally not enabled by default; Django's
deployment check reports this optional warning until an operator opts into
browser preload enrollment for their domain.

## Configuration

All settings are environment-driven; see `deployment.example` and
`config/settings.py` for the complete list.

| Setting                | Default / purpose                                               |
| ---------------------- | --------------------------------------------------------------- |
| `SECRET_KEY`           | Required; no application fallback                               |
| `INVITATION_CODE`      | Required to register                                            |
| `DEBUG`                | `false`                                                         |
| `DATABASE_URL`         | Local SQLite; Docker uses `sqlite:////data/db.sqlite3`          |
| `TIME_ZONE`            | `Europe/Berlin`                                                 |
| `WEISSWURST_WEEKDAY`   | `3`, Thursday for Weisswurst (Monday = 0)                       |
| `LEBERKAESE_WEEKDAY`   | `4`, Friday for Leberkaese                                      |
| `DEADLINE_DAYS_BEFORE` | `1`                                                             |
| `DEADLINE_TIME`        | `18:00`, local timezone                                         |
| `UPCOMING_WEEKS`       | `8`, minimum 8                                                  |
| `PUBLIC_BASE_URL`      | Canonical URL used for PayPal redirects                         |
| `PAYPAL_ENVIRONMENT`   | `sandbox` or `live`                                             |
| `PAYPAL_API_BASE`      | Optional override; environment-specific official API by default |

Configuration changes apply to newly generated events. Existing event deadlines
are retained. Products and prices are editable shared application data, not
hard-coded configuration. The ORM is portable to PostgreSQL; install
`psycopg[binary]`, set `DATABASE_URL`, and migrate. Existing data migration and
PostgreSQL operational testing are separate operator tasks.

Weisswurst and Leberkaese have separate weekly schedules and product selections.
Leberkaese starts with only Leberkassemmel at EUR 2.00; the migration adds this
product automatically. Both schedules close at 18:00 on the preceding day by
default. Saved default quantities apply only to the matching event type, and at
least eight upcoming dates are generated for each type. Product type is fixed
after creation so existing orders and defaults cannot change categories.

## Accounting rules

- Monetary amounts are integer cents. Forms parse decimal strings without floats.
- Saving an order records its cost in the ledger immediately. Future orders only
  affect the account balance from their event date (Europe/Berlin by default).
  Future-dated ledger entries are excluded until their booking date. Cash balance,
  participant balances, account history and PayPal debt settlement use this same
  cutoff. Later edits record only the difference; cancellations reverse the charge.
- Saved item prices are snapshots. A product price change alone never reprices
  an order. Saving an editable order uses current prices for active products.
- Regular participants can edit only `OPEN` events before their deadline.
  Active users with `is_admin=True` can also correct locked or settled orders
  through the same order form, without reopening the event. The ledger records
  the price difference and the acting admin. Stale edits are still rejected.
  In a date's detail view, admins can also select a participant without an order
  and use **Bestellung hinzufuegen**, including after the deadline. New orders
  start empty; quantities are entered explicitly and charged when saved.
- Every active participant gets an order when an event is created. Default orders
  are copied once; editing defaults does not change existing orders. Registration
  also creates empty orders for all existing open events.
- All authenticated users share permissions: view participants, histories and
  orders, edit anyone's open orders, edit products, and correct manual payments.
  Profile, defaults, manual deposits and PayPal initiation belong to the signed-in
  user. Admin status is assigned through server-side account management only;
  registration and profile forms cannot grant it. There is no separate admin UI.
- The group is a **trusted private group**: a manual payment is self-reported and
  immediately credited. Its author is recorded; this is not bank verification.
- The immutable ledger is authoritative. There is no cached balance to drift.
  A manual correction creates one opposite entry; record a replacement payment
  separately when needed. Orders and PayPal entries cannot be manually reversed.
- Event generation, manual submission, PayPal capture and refunds are idempotent.
  User-row locks serialize balance-affecting operations. SQLite uses immediate
  write transactions and a busy timeout.

```fish
uv run python manage.py reconcile
uv run python manage.py reconcile --paypal
```

Reconciliation compares order totals and recorded PayPal principal/refunds against
their ledger entries. It fails with a nonzero exit code on mismatch. With
`--paypal`, provider data is refreshed first, including delayed confirmations and
refunds. Reconciliation never rewrites historic entries. Database access itself
must be restricted; application immutability is not protection against an operator
directly modifying the database.

## PayPal setup

1. Create a PayPal REST app for the single receiving merchant account. Set
   `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_MERCHANT_ID` and
   `PAYPAL_ENVIRONMENT=sandbox` initially.
2. Set `PUBLIC_BASE_URL` to your public HTTPS URL. Register a webhook at
   `https://your-domain/paypal/webhook/` and put its ID in `PAYPAL_WEBHOOK_ID`.
3. Subscribe to `CHECKOUT.ORDER.APPROVED`, `CHECKOUT.ORDER.COMPLETED`,
   `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.PENDING`,
   `PAYMENT.CAPTURE.DENIED` and `PAYMENT.CAPTURE.REFUNDED`.
4. Test with a **different sandbox buyer account**. Initiate a top-up in **Kasse**,
   approve it at PayPal, and use **Abschliessen & pruefen** after returning.
   A verified approved webhook or the worker can also finish the capture.
5. Check duplicate delivery, pending-to-completed transitions, partial/full
   refunds from the merchant dashboard, and `reconcile --paypal`.
6. For production, change to `live` and use the corresponding live app, merchant,
   webhook ID and credentials. Never send real funds to validate sandbox code.

Browser return/cancel URLs never credit money. Webhooks are CSRF-exempt only at
their exact endpoint, and must pass PayPal's server-side signature verification.
The backend then fetches the associated order and verifies merchant ID, local
payment UUID, amount and EUR currency before posting a unique capture reference.
Refunds are separate negative entries keyed by provider refund ID. Retried and
out-of-order events cannot duplicate a credit. Unknown matching notifications
return a retryable status, and the worker provides independent reconciliation.

Outstanding-balance amounts are calculated server-side and checked again before
capture. If another payment reduces the debt during checkout, the old checkout
cannot be captured; start a new payment. Choosing a top-up explicitly permits
additional credit. Stored PayPal email is separate from login email; payment
assignment uses the local payment ID, never an untrusted email match.

Only the backend receives API credentials. Do not log request bodies, cookies,
authorization headers, or full PayPal return URLs at the reverse proxy. PayPal
adds its own temporary order token to redirects; the application ignores it and
redirects to a clean local page. No password or API secret is placed in a URL.

PayPal network verification cannot be fully certified without operator-provided
sandbox credentials and buyer approval. Automated provider-contract tests use
mocked responses; complete the above real sandbox checklist before production.

## Backups and restoration

Use the SQLite online backup API, not a live filesystem copy:

```fish
docker compose exec web /app/.venv/bin/python manage.py backup_db /data/backups/breakfast-2026-10-01.sqlite3
docker compose cp web:/data/backups/breakfast-2026-10-01.sqlite3 ./backups/
```

Backups include all profiles, password hashes, orders, ledger entries and PayPal
references, so protect and encrypt them. The backup command refuses to overwrite
an existing file and creates files with mode 0600. Retain off-host copies and
test restores periodically.

To restore, stop **both** web and worker, restore the backup as `/data/db.sqlite3`
in the named volume (UID/GID 10001), then restart and run `reconcile --paypal`.
Preserve the same environment/merchant configuration. Restore before permitting
new writes. Never delete the volume as part of a routine update. PostgreSQL
deployments should use `pg_dump` and their corresponding restore procedure.

## Tests

```fish
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
uv run coverage run --source=weisswurstrunde manage.py test
uv run coverage report
docker build -t weisswurstrunde .
```

Tests cover authentication, invitation/uniqueness checks, CSRF and access control,
order deadlines and price snapshots, default propagation, amount validation,
immutable accounting and corrections, PayPal states/refunds/idempotence, webhook
verification, reconciliation, command idempotence and backups. CI runs on Python
3.13 and builds the container. Ordinary tests do not send money or call PayPal.

### Sandbox smoke test

The real sandbox smoke test is opt-in. With your sandbox credentials already in
the environment:

```fish
env RUN_PAYPAL_SANDBOX_TESTS=true uv run python manage.py test weisswurstrunde.tests.test_paypal.SandboxSmokeTests
```

It creates an unapproved sandbox checkout without capturing money. The manual
buyer-approval and webhook checklist above is still required before production.
Closing an unfinished checkout in the UI prevents further automatic capture;
any genuinely received payment subsequently verified by PayPal is still booked.

## License and assets

Application source: MIT, see `LICENSE`. Third-party asset licenses and sources
are listed in `static/ASSETS.md`. All fonts, icons and the breakfast photograph
are served locally.
