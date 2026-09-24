# Weißwurstrunde – Requirements

## 1. Purpose

The application digitizes a private, recurring weekly Weißwurst breakfast group.

Users shall be able to place and modify orders for upcoming dates. Orders are stored per user and date. The application calculates the resulting balance for each user.

Users can either:

- pay manually
- pay via PayPal
- preload a credit balance via PayPal

The application shall automatically account for all orders and payments and maintain the current balance for every user.

The application is self-hosted and fully open source.

---

## 2. Users and Registration

### 2.1 Registration

New users can register themselves.

Required fields:

- Name
- Email address
- Password
- PayPal email address

The email address must be unique.

The PayPal email address may be different from the application login email address.

Passwords must never be stored in plaintext.

Registration must be protected by a shared invitation code.

### 2.2 Authentication

Registered users can log in using their email address and password.

Unauthenticated users must not be able to view orders, user information, balances, or payment data.

### 2.3 User Permissions

There are no different user roles.

All authenticated users have the same permissions.

Every authenticated user may:

- view all users
- view all past and future orders
- modify all orders
- configure their own default order
- modify their own user data
- view payment records
- view balances
- initiate PayPal payments

No separate admin interface is required.

---

# 3. Products

Products are managed centrally.

Each product has at least:

- Name
- Unit
- Price
- Active/inactive status

Examples:

- Weißwurst
- Pretzel
- Mustard
- Weißbier

The application must support arbitrary additional products.

Prices must be stored as integer cents.

Floating-point numbers must not be used for storing monetary values.

---

# 4. Weekly Events

The Weißwurst breakfast normally takes place once per week.

A weekly event has at least:

- Date
- Order deadline
- Status

Possible statuses:

- `OPEN`
- `LOCKED`
- `SETTLED`

### 4.1 Upcoming Events

The application shall display multiple upcoming events.

At least 8 weeks into the future should be supported.

### 4.2 Order Deadline

Orders may be freely modified until the order deadline.

After the deadline, normal users must no longer be able to modify orders.

Orders remain visible after the deadline.

---

# 5. Orders

An order belongs to:

- one user
- one weekly event

An order consists of zero or more order items.

Each order item contains:

- Product
- Quantity
- Unit price at the time of ordering

The unit price valid when the order is created or modified must be stored with the order item.

Changing the current product price must never change existing orders.

---

# 6. Default Orders

Each user can define a personal default order.

Example:

```text
Weißwürste: 2
Pretzels:   1
```

When a new weekly event is created, the user's default order shall automatically be copied into an order for that event.

The user can then modify the order for that specific event.

Changing an order for one event must not modify the user's default order.

Changing the default order must not modify existing orders.

---

# 7. Ordering UI

For each upcoming open event, the user shall see their current order.

Example:

```text
Thursday, October 1, 2026

Weißwürste    [ 2 ▼ ]
Pretzels      [ 1 ▼ ]

Total: €3.20

[ Save order ]
```

Quantities must support at least the value `0`.

Negative quantities are not allowed.

The total price should be displayed immediately after changing quantities.

---

# 8. Order Overview

Every authenticated user can view all orders.

The overview shall contain at least:

- Event date
- User
- Products
- Quantities
- Total amount

The view shall support filtering or sorting by event date.

---

# 9. Accounting and User Balances

The application maintains a financial balance for every user.

The balance represents the difference between payments/credit and consumed goods.

The conceptual calculation is:

```text
Payments / credited funds
-
Order costs
=
Current balance
```

A positive balance represents available credit.

A negative balance represents an outstanding amount.

Example:

```text
Initial credit:       €20.00

Order 1:              -€6.40
Order 2:              -€7.20

Current balance:       €6.40
```

Another example:

```text
Order 1:               €6.40
Order 2:               €7.20

Payments:             €10.00

Outstanding balance:   €3.60
```

All monetary values must be stored as integer cents.

### 9.1 Automatic Balance Verification

The backend shall calculate and verify the balance for every user.

The displayed balance must always be derivable from the underlying payment and order records.

The balance must not depend solely on a manually maintained numeric field.

The backend should be able to detect inconsistencies between:

- recorded payments
- recorded orders
- calculated balance
- stored balance, if a cached balance is used

Balance calculations must be performed server-side.

---

# 10. Payment Ledger

All financial transactions must be represented in a unified payment/accounting ledger.

The system must distinguish at least between:

- PayPal top-up
- Manual payment
- Order charge
- Refund or correction

Every transaction shall contain at least:

- User
- Amount in cents
- Transaction type
- Date/time
- Status
- Optional external reference
- Optional note

The ledger is the authoritative source for calculating the user's balance.

Payment transactions must not be deleted in normal operation.

Corrections should be represented by compensating transactions rather than modifying historical financial records.

---

# 11. Manual Payments

Manual payments remain supported even when PayPal integration is enabled.

An authenticated user can record a manually made payment.

Example:

```text
Payment amount: €20.00
Payment method: Cash
Note: Paid at breakfast

[ Record payment ]
```

Manual payments immediately affect the user's balance once recorded.

Supported manual payment methods should include at least:

- Cash
- Bank transfer
- Other

Manual payments must use the same accounting ledger as PayPal payments.

---

# 12. PayPal Integration

The application shall support direct PayPal API integration.

The purpose of the integration is primarily:

1. Allow users to preload credit.
2. Allow users to pay an outstanding balance.
3. Automatically recognize successful PayPal payments.
4. Assign received payments to the correct user.

All PayPal payments go to one configured PayPal merchant account.

The merchant account is configured globally by the application operator.

Users do not need individual PayPal merchant accounts.

---

# 13. PayPal User Data

Each user must provide their PayPal email address.

The PayPal email address is associated with the user's account.

The PayPal email address may differ from the email address used to log into the application.

The application must not assume that the user's login email and PayPal email are identical.

The PayPal email address must be editable by the user.

---

# 14. PayPal Credit Top-Up

Users can preload money into their account.

Example:

```text
Current balance: €4.60

Add credit

[ €10 ] [ €20 ] [ €50 ] [ Custom amount ]

[ Pay with PayPal ]
```

The selected amount is sent to the configured merchant PayPal account.

After successful payment confirmation, the amount is credited to the user's account.

Example:

```text
Before:
Balance: €4.60

PayPal top-up:
+€20.00

After:
Balance: €24.60
```

The credit can subsequently be used automatically to pay for orders.

---

# 15. PayPal Outstanding Balance Payment

If a user's balance is negative, the application shall offer a PayPal payment for the outstanding amount.

Example:

```text
Current balance: -€13.60

[ Pay €13.60 with PayPal ]
```

After successful PayPal confirmation:

```text
Before:  -€13.60
Payment: +€13.60
After:    €0.00
```

The payment amount must not exceed the amount required to settle the outstanding balance unless the user explicitly chooses to add additional credit.

---

# 16. PayPal Payment Verification

The backend must not trust client-side confirmation of a PayPal payment.

A payment must only be credited after successful server-side verification through the PayPal API.

The system must correctly handle:

- successful payments
- cancelled payments
- failed payments
- pending payments
- rejected payments
- duplicate notifications
- repeated API callbacks
- delayed confirmation

PayPal transaction IDs must be stored where applicable.

The same PayPal transaction must never be credited more than once.

The payment processing logic must be idempotent.

---

# 17. PayPal Webhooks

Where supported and appropriate, the application shall use PayPal webhooks to receive asynchronous payment status updates.

Webhook requests must be verified according to PayPal's security requirements before being processed.

The application must be able to reconcile webhook events with locally stored PayPal transactions.

A webhook must never directly modify the user's balance without validating the associated transaction.

---

# 18. PayPal Refunds and Corrections

PayPal refunds must be represented correctly in the accounting ledger.

A refund must reverse or reduce the corresponding credited amount.

Historical transactions must remain auditable.

The application must not simply overwrite the original payment record.

---

# 19. User Dashboard

After logging in, the user sees a dashboard.

The dashboard shall contain at least:

### Current balance

Positive balance:

```text
Credit: €13.60
```

Negative balance:

```text
Outstanding: €13.60
```

### Upcoming events

Example:

```text
Oct 1, 2026     2 W / 1 P    €3.20
Oct 8, 2026     2 W / 1 P    €3.20
Oct 15, 2026    0 W / 0 P    €0.00
```

### Payment options

If the balance is negative:

```text
[ Pay outstanding balance ]
```

The user should also have an option to add credit:

```text
[ Add credit ]
```

---

# 20. User Profile

Each user can modify their own profile data:

- Name
- Email address
- Password
- Default order
- PayPal email address

The application login email and PayPal email must be stored separately.

---

# 21. User Overview

All authenticated users can see an overview of all participants.

At minimum:

- Name
- Current balance

Example:

```text
Participants

Stefan       +€13.60
Thomas         €0.00
Michael       -€7.20
Peter         +€4.00
```

---

# 22. Transaction History

Each user can see their complete financial history.

The history shall include:

- Orders
- Manual payments
- PayPal payments
- Credit top-ups
- Refunds
- Corrections

Each entry should show:

- Date/time
- Description
- Amount
- Resulting balance
- Payment method where applicable

The user must be able to understand how their current balance was calculated.

---

# 23. Price Changes

Products can receive a new price.

New orders use the new price.

Existing order items retain their original price.

Changing the product price must never modify historical orders.

---

# 24. Validation

The application must perform server-side validation.

At minimum:

- Quantities must not be negative
- Prices must not be negative
- Payment amounts must be positive
- Email addresses must be valid
- Email addresses must be unique
- PayPal email addresses must be valid
- Normal users cannot modify orders after the order deadline
- Unauthenticated users cannot access protected pages
- Invalid IDs must not cause server errors
- A PayPal transaction cannot be credited more than once

---

# 25. Security

The application must implement at least:

- Secure password hashing
- CSRF protection for state-changing requests
- Session-based authentication
- Protection against SQL injection
- Server-side input validation
- Secure session cookies
- No sensitive data in URLs
- Appropriate security headers
- No passwords or secrets committed to the Git repository
- Configuration through environment variables

PayPal API credentials must never be exposed to the browser.

The browser must not be trusted for payment amounts, transaction status, or account balances.

All payment-related state transitions must be validated by the backend.

---

# 26. Responsive UI

The application must be usable on:

- Desktop
- Tablet
- Smartphone

The primary focus is simple and fast operation on smartphones.

Placing or modifying an order should require as few interactions as reasonably possible.

---

# 27. Usability

The most common operation is:

> Modify the order for the next Weißwurst breakfast.

This operation should be possible without navigating through multiple pages.

The application shall provide clear feedback for:

- successful saves
- failed saves
- expired order deadlines
- invalid input
- successful PayPal payments
- pending PayPal payments
- failed PayPal payments
- successful credit top-ups

---

# 28. Technical Requirements

Recommended technology stack:

- Python (use uv)
- Django
- Django ORM
- HTMX
- Tailwind CSS or another lightweight CSS framework
- SQLite

The application shall be deployable as a Docker container.

The use of the Django ORM should allow PostgreSQL to be introduced later without fundamental changes to the business logic.

No separate frontend project is required.

No separate REST API is required for the first release.

PayPal API communication is performed exclusively by the backend.

---

# 29. PayPal Configuration

The following PayPal configuration must be externalized through environment variables or equivalent secure configuration:

- PayPal environment (`sandbox` / `live`)
- Client ID
- Client secret
- Merchant account identifier where required
- Webhook configuration
- API endpoints

PayPal credentials must never be stored in the database or committed to source control.

The application must support PayPal Sandbox for development and testing.

Production must use PayPal Live APIs.

---

# 30. Configuration

Configuration values must not be hard-coded into the source code.

At minimum:

- Secret key
- Database configuration
- Debug mode
- Allowed hosts
- Timezone
- Invitation code, if enabled
- PayPal configuration

The default timezone is `Europe/Berlin`.

---

# 31. Automation

The application shall support automatic creation of new weekly events.

An automated process shall:

1. Create the next weekly event.
2. Find all active users.
3. Copy each user's default order.
4. Create the corresponding order for the new event.

The process must be idempotent.

Running the process multiple times must not create duplicate events or duplicate orders.

---

# 32. Tests

The application shall contain automated tests for at least:

### Users

- Registration
- Login
- Logout
- Invalid login
- Email uniqueness
- PayPal email handling

### Products

- Product creation
- Price changes
- Price changes do not modify historical orders

### Orders

- Create order
- Modify order
- Quantity validation
- Order deadline
- Default orders
- Multiple future weeks

### Accounting

- Order totals
- Manual payments
- PayPal payments
- Credit top-ups
- Outstanding balance
- Positive credit balance
- Multiple payments
- Refunds
- Corrections
- Balance reconciliation

### PayPal

- PayPal sandbox integration
- Successful payment
- Cancelled payment
- Failed payment
- Pending payment
- Webhook verification
- Duplicate webhook
- Duplicate transaction
- Payment reconciliation
- Credit top-up
- Outstanding balance payment

### Permissions

- Protected pages reject unauthenticated users
- Authenticated users can view all permitted data
- Authenticated users can modify all permitted data

### Automation

- Weekly event creation
- Default order propagation
- Idempotent execution

---

# 33. Accounting Integrity

The accounting system must be designed as an auditable ledger.

Financial history must not be destructively modified.

If an error needs to be corrected, the preferred mechanism is a compensating transaction.

For example:

```text
Original payment:      +€20.00
Correction:            -€20.00
Replacement payment:   +€15.00
```

The resulting balance must be calculated from the complete transaction history.

The backend should provide a reconciliation mechanism that can detect accounting inconsistencies.

---

# 34. Backup

The application must allow the database to be backed up easily.

For SQLite, a backup of the database file is sufficient initially.

The application must not store non-reproducible application data exclusively inside the container filesystem.

PayPal transaction IDs and accounting records must be included in database backups.

---

# 35. Explicitly Out of Scope

The following features are not required for the first release:

- Native Android application
- Native iOS application
- PayPal merchant accounts for individual users
- Cryptocurrency payments
- Other payment providers
- In-app card payment processing
- Different user roles
- Separate admin interface
- Push notifications
- Chat
- Multi-tenancy
- Public registration without authentication
- Complex permission system
- REST API
- GraphQL
- Microservices

---

# 36. Definition of Done

The first release is considered complete when:

1. A new user can register and log in.
2. A user can configure their default order.
3. Multiple future Weißwurst breakfast dates are visible.
4. An order exists for each relevant user and date.
5. Orders can be modified through a simple UI.
6. Orders are protected from modification after the deadline.
7. All authenticated users can see all orders.
8. All authenticated users can modify orders according to the defined rules.
9. Prices are stored correctly and historical prices remain unchanged.
10. The backend calculates and verifies the balance for every user.
11. Manual payments can be recorded.
12. Users can add credit through PayPal.
13. Users can pay outstanding balances through PayPal.
14. PayPal payments are verified server-side.
15. PayPal transactions cannot be credited more than once.
16. PayPal webhooks are securely validated.
17. PayPal Sandbox can be used for development and testing.
18. PayPal Live mode can be configured for production.
19. PayPal credentials are never exposed to the frontend.
20. Users can see their complete transaction history.
21. A PayPal payment can be reconciled with the correct user.
22. Users can modify their own profile information.
23. The application works on smartphones and desktops.
24. Core business logic is covered by automated tests.
25. PayPal payment and webhook handling is covered by automated tests.
26. The application can be started reproducibly using Docker.
27. The database can be backed up easily.
28. No passwords, secrets, PayPal credentials, or other confidential configuration values are committed to the repository.
