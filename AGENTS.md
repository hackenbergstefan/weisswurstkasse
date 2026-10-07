# Repository Instructions

## Purpose and Stack

This repository is a self-hosted breakfast order book and shared cash ledger for
a trusted private group. Its Python package and Django app are `weisswurstrunde`;
the user-facing brand is Die Dein Stammtisch. Preserve the existing German UI.

- Use Python 3.12+, Django 5.2 LTS, uv, and SQLite by default.
- Keep the server-rendered Django templates and progressive JavaScript approach.
  There is no frontend build pipeline or external CDN requirement.
- Read [README.md](README.md) for setup, accounting rules, PayPal operations,
  deployment, and backups. Check the implementation when documentation disagrees.
- Dependency constraints and Ruff configuration live in
  [pyproject.toml](pyproject.toml); preserve [uv.lock](uv.lock).

## Working Method

1. Inspect `git status --short` before editing. Preserve unrelated changes,
   untracked files, and user deletions. Do not commit or create branches unless asked.
2. Start at the affected view, service, template, or failing test. Read the nearby
   implementation and test before changing behavior; do not invent APIs or fields.
3. State the expected behavior and choose a focused check that can disprove your
   proposed fix. Make the smallest change, run that check, and fix failures locally.
4. Preserve the existing architecture, naming, formatting, and test helpers. Avoid
   unrelated refactors, new frameworks, speculative abstractions, and dependencies.
5. Add a regression test for changed behavior. Update documentation when changing
   an operator command, environment variable, or business rule.
6. Report what changed, the exact checks run, and any remaining unverified behavior.
   Do not claim live PayPal verification from mocked tests.

Use fish-compatible commands. Prefer VS Code-integrated tools where available.
Do not read or print `.env`, credentials, cookies, database contents, audit logs,
or personal data for routine exploration. Use
[deployment.example](deployment.example) and
[config/settings.py](config/settings.py) to discover configuration names.
Never send real payments as a test.

Preserve existing action logging through [audit.record](weisswurstrunde/audit.py).
Audit logs contain actor emails and action details; they do not replace the ledger.
`AUDIT_LOG_FILE` defaults to `audit.log` in the repository root.

## Non-Negotiable Business Rules

- Store money as integer cents; parse user amounts without floats.
- The immutable ledger is authoritative. Do not add cached balances or rewrite
  historic entries to make reconciliation pass.
- Future orders affect balances only from their event date. Preserve the same
  booking-date cutoff for balances, history, cash reporting, and debt settlement.
- Saved item prices are snapshots. Changing a product price does not itself
  reprice an existing order.
- This is a shared application: authenticated users may edit other participants'
  open orders. Do not introduce owner-only or admin-only restrictions everywhere.
- Regular edits require an open event before its deadline. Admin corrections do
  not reopen locked or settled events, and must still reject stale submissions.
- Manual payments are self-reported credits, not bank-verified transactions.
- PayPal uses PayPal.me links and IMAP notification matching. Browser redirects
  never prove payment. Only matching incoming notifications from the configured
  mailbox create PayPal credits; ambiguous matches stay pending, and retries
  must not duplicate ledger entries.

## Baseline Verification

Run commands from the repository root. The command-scoped settings below are for
local checks only: `SECRET_KEY` is required, `DEBUG=true` allows test HTTP requests,
and `AUDIT_LOG_FILE=/dev/null` keeps test activity out of the application audit log.
For application setup, follow [README.md](README.md); never reuse test keys or
disable audit logging in production.

```fish
uv sync --frozen
uv run ruff check .
env SECRET_KEY=test-only-key DEBUG=true AUDIT_LOG_FILE=/dev/null \
    uv run python manage.py makemigrations --check --dry-run
env SECRET_KEY=test-only-key DEBUG=true AUDIT_LOG_FILE=/dev/null \
    uv run python manage.py test
node --test tests/orders.test.cjs
```

Start with a single relevant Django test module, class, or method before running
the wider suite. Use the existing Django test runner, not an assumed pytest setup.
Do not migrate, seed, reconcile, or run a worker against a real database just to
validate code; Django tests use their test database.
