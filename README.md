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
uv run python manage.py seed_demo
uv run python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/register/ and use the invitation code from your local
environment. Keep the environment in your terminal while running management
commands. `seed_products` is optional and adds **example prices**; review them in
**Sortiment** before real use. `seed_demo` creates three local demo accounts,
upcoming orders and cash balances. Their password is `demo`.

Run `uv run python manage.py worker` in another terminal with the same environment
for automatic event generation and PayPal reconciliation. The worker runs every
15 minutes; `worker --once` performs one cycle.

## Design

The application uses a single **Die Dein Stammtisch** design: blue and yellow,
Bangers display type, a local Augsburg illustration, product cards and a live
order receipt. There is no theme selector or browser-stored design preference.
The reference is `design-previews/Dein Stammtisch.html`; its illustration
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
docker compose exec web /app/.venv/bin/python manage.py seed_demo
```

The web container runs migrations before accepting traffic. The worker starts
after the web health check succeeds. Both services use the workspace-local
`db.sqlite3` through a bind mount at `/data/db.sqlite3`. Back up that file before
upgrades. The image runs as UID/GID 10001, not root. Start only one scheduler per
installation.
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

| Setting                | Default / purpose                                                       |
| ---------------------- | ----------------------------------------------------------------------- |
| `SECRET_KEY`           | Required; no application fallback                                       |
| `INVITATION_CODE`      | Required to register                                                    |
| `DEBUG`                | `false`                                                                 |
| `DATABASE_URL`         | Local SQLite; Docker uses the bind-mounted `sqlite:////data/db.sqlite3` |
| `TIME_ZONE`            | `Europe/Berlin`                                                         |
| `WEISSWURST_WEEKDAY`   | `3`, Thursday for Weisswurst (Monday = 0)                               |
| `LEBERKAESE_WEEKDAY`   | `4`, Friday for Leberkaese                                              |
| `DEADLINE_DAYS_BEFORE` | `1`                                                                     |
| `DEADLINE_TIME`        | `18:00`, local timezone                                                 |
| `UPCOMING_WEEKS`       | `8`, minimum 8                                                          |
| `PUBLIC_BASE_URL`       | Canonical URL used for asset and email links                            |
| `PAYPAL_ME_LINK`       | PayPal.me link of the receiving account                                  |
| `PAYPAL_IMAP_HOST`     | IMAP server receiving PayPal notifications                               |
| `PAYPAL_IMAP_PORT`     | IMAP SSL port, defaults to `993`                                         |
| `PAYPAL_IMAP_USER`     | IMAP login                                                                |
| `PAYPAL_IMAP_PASSWORD` | IMAP password                                                             |
| `PAYPAL_IMAP_FOLDER`   | IMAP folder, defaults to `INBOX`                                         |
| `PAYPAL_IMAP_USE_TLS`  | Enable IMAP STARTTLS, mutually exclusive with SSL                        |
| `PAYPAL_IMAP_USE_SSL`  | Enable implicit IMAP TLS, defaults to `true`                              |
| `EMAIL_HOST`           | SMTP server hostname                                                     |
| `EMAIL_PORT`           | SMTP server port, defaults to `25`                                      |
| `EMAIL_HOST_USER`      | SMTP login user                                                          |
| `EMAIL_HOST_PASSWORD`  | SMTP login password                                                      |
| `EMAIL_USE_TLS`        | Enable STARTTLS, typically `true` with port `587`                       |
| `EMAIL_USE_SSL`        | Enable implicit TLS, typically `true` with port `465`                   |
| `DEFAULT_FROM_EMAIL`   | Sender address for order notifications                                   |

Configuration changes apply to newly generated events. Existing event deadlines
are retained. Products and prices are editable shared application data, not
hard-coded configuration. The ORM is portable to PostgreSQL; install
`psycopg[binary]`, set `DATABASE_URL`, and migrate. Existing data migration and
PostgreSQL operational testing are separate operator tasks.

At the order deadline, every participant receives their final order and balance
for the event date. Active admins receive the complete order list. Configure a
working SMTP server before operating the worker; it sends these notifications
after the event status has been changed to `LOCKED`.

Weisswurst and Leberkaese have separate weekly schedules and product selections.
Leberkaese starts with only Leberkassemmel at EUR 2.00; `seed_products` adds the
example assortment explicitly. Both schedules close at 18:00 on the preceding
day by default. Saved default quantities apply only to the matching event type,
and at least eight upcoming dates are generated for each type. Product type is
fixed after creation so existing orders and defaults cannot change categories.

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
- Event generation, manual submission and PayPal-Mailabgleich are idempotent.
  User-row locks serialize balance-affecting operations. SQLite uses immediate
  write transactions and a busy timeout.

```fish
uv run python manage.py reconcile
uv run python manage.py reconcile --paypal
```

Reconciliation compares order totals and recorded PayPal entries against their
ledger entries. With `--paypal`, the configured IMAP mailbox is read and matching
incoming payments and outgoing payouts are recorded idempotently. Reconciliation
never rewrites historic entries.

## PayPal setup

1. Create a PayPal.me link for the receiving account and set `PAYPAL_ME_LINK`.
2. Configure an IMAP mailbox that receives PayPal notifications with
  `PAYPAL_IMAP_HOST`, `PAYPAL_IMAP_PORT`, `PAYPAL_IMAP_USER`,
  `PAYPAL_IMAP_PASSWORD` and optionally `PAYPAL_IMAP_FOLDER`.
3. A participant starts a payment in **Kasse**, follows the generated PayPal.me
  link and then uses **PayPal-Mail prüfen**. The worker also runs this check every
  15 minutes. The stored PayPal email and amount must identify one open payment.
4. Admins use the PayPal link in the separate **PayPal-Auszahlung** section and
  send the payment manually. Every matching outgoing PayPal notification creates
  a completed payout automatically, including recipient name and transaction code
  in its note.
5. Successfully matched incoming messages are moved to
  `Einzahlungen/<User-ID>-<Name>`. Outgoing messages are moved to `Auszahlungen`;
  both folders are created automatically when needed.

PayPal redirects, browser state and self-reported payment forms never credit the
ledger. Only a matching message from the configured mailbox does that. Message
IDs and provider references are stored so repeated IMAP scans cannot duplicate
ledger entries. Ambiguous amount matches are left pending for manual resolution.

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
immutable accounting and corrections, PayPal mail matching/idempotence,
reconciliation, command idempotence and backups. CI runs on Python
3.13 and builds the container. Ordinary tests do not send money or call PayPal.

## License and assets

Application source: MIT, see `LICENSE`. Third-party asset licenses and sources
are listed in `static/ASSETS.md`. All fonts, icons and the breakfast photograph
are served locally.
