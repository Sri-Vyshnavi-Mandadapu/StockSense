# StockSense

**Inventory, in sync.** A lean, modular inventory management app for inventory managers and warehouse staff. Python + SQLite + a responsive JavaScript interface, with **no third-party runtime dependencies**.

## Run locally

Requires Python 3.11 or newer (tested on Python 3.14).

```powershell
cd C:\Users\91998\OneDrive\Desktop\StockSense
python run.py
```

Open **http://127.0.0.1:8000** and create an account. The first account becomes the inventory manager; later accounts are warehouse staff in the same organization. Managers maintain products and warehouse settings. Both roles can perform stock operations.

1. In **Settings**, add a warehouse and its first location; add more locations as needed.
2. In **Products**, create a SKU, category, unit, and reorder threshold. Optional opening stock creates an audited receipt.
3. Create a **Receipt**, add a supplier and product quantities, then Confirm → Mark ready → Validate.
4. Create deliveries, internal transfers, and physical count adjustments from the sidebar.
5. Inspect **Move History** for the actor, signed change, location, and resulting balance.

No demo credentials or sample stock are inserted into your database.

## Included features

- Signup, login, logout, editable profile, PBKDF2 password hashing, server-side expiring sessions, and OTP password reset.
- Inventory dashboard: products in stock, low/out-of-stock products, pending receipts/deliveries, and scheduled transfers.
- Search by SKU, product, partner, or document reference; filter by operation, status, warehouse, location, and category.
- Product creation/editing, categories, units of measure, per-location availability, and reorder thresholds.
- Multiple warehouses and storage locations.
- Multi-product receipts and deliveries; explicit picking and packing before dispatch.
- Internal transfers with balanced source and destination ledger entries.
- Physical count adjustments, including zero counts, with differences calculated at validation time.
- Draft → Waiting → Ready → Done workflow, plus cancellation before completion.
- Atomic stock validation, insufficient-stock protection, duplicate-validation prevention, and immutable ledger records.
- Responsive desktop/mobile layouts and 15-second polling while the interface is idle.

Dashboard product counts reflect warehouse/location/category/search filters. Document type and status filters apply to document lists and pending-document KPIs. Reorder thresholds are compared with availability in the selected location scope. They produce alerts, not automatic purchase orders. Drafts and ready documents do not reserve stock: availability is checked atomically on validation. Quantities support three decimal places; no currency calculations or unit conversions are performed.

## OTP email configuration

Set environment variables before starting the server. `.env.example` documents all settings; `.env` files are **not automatically loaded**.

```powershell
$env:SMTP_HOST = 'smtp.your-provider.com'
$env:SMTP_PORT = '587'
$env:SMTP_USER = 'your-smtp-user'
$env:SMTP_PASSWORD = 'your-smtp-password'
$env:SMTP_FROM = 'stocksense@your-company.com'
python run.py
```

Use a STARTTLS SMTP provider. Reset codes expire after 10 minutes, allow five attempts, and can be used only once. Resetting a password revokes existing sessions. Auth requests are rate limited per socket IP (20 requests per 10 minutes). Unknown-account reset requests return the same success message.

For local testing only, set `$env:STOCKSENSE_DEV_OTP = '1'` to print codes in the server terminal when SMTP is absent. This is off by default. SMTP delivery requires your provider credentials and has not been verified against a live mail service.

## Architecture

```text
web/                   Responsive UI, forms, filters, API client
stocksense/server.py   HTTP routes, access checks, security headers
stocksense/auth.py     Accounts, sessions, reset codes, SMTP transport
stocksense/inventory.py Inventory rules and atomic stock movements
stocksense/db.py       Schema, connections, transaction boundary
tests/                 Domain, authentication, concurrency, browser tests
```

The browser calls a same-origin JSON API. SQLite foreign keys, WAL, and `BEGIN IMMEDIATE` serialize stock writes, so a failing line rolls back the entire document. Ledger triggers prevent updates and deletes. Passwords and reset codes are salted and hashed; session tokens are random, stored hashed, and sent in HttpOnly/SameSite cookies. Mutations require a custom request header and JSON content type; no cross-origin access is enabled.

API endpoints: `POST /api/signup`, `/api/login`, `/api/logout`, `/api/forgot`, `/api/reset`, `/api/profile`, `/api/products`, `/api/warehouses`, `/api/documents`, `/api/transition`; `GET /api/me`, `/api/state`. State includes catalog, warehouse/location lists, balances, documents, lines, and the latest 1,000 ledger entries. Persistent stock changes are available only through audited operations.

## Tests

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
# Optional browser tests (development dependencies only)
npm ci
npm run test:e2e
```

Browser tests use installed Google Chrome by default. To use bundled Chromium: `npx playwright install chromium`, then set `$env:PLAYWRIGHT_CHANNEL = 'chromium'`. Tests start an isolated server and database, execute the 100 kg receipt → transfer → 20 kg delivery → 77 kg count flow, and save screenshots under `test-results/`.

GitHub Actions runs Python tests and JavaScript syntax checks on pushes and pull requests.

## Deployment and scaling

This is a single-organization, single-instance starter app. All registered accounts join the same inventory workspace. Bootstrap the first manager on a trusted machine before exposing registration; use an identity provider/invitation policy for public installations. For production, place the app behind a TLS reverse proxy with request limits/timeouts and set `STOCKSENSE_SECURE_COOKIE=1`. The included Python HTTP server is intended for local use and small controlled deployments, not direct public exposure.

The default database is `data/stocksense.db`. Back it up using SQLite's backup API or stop the app before copying the database and its WAL files. Prefer `STOCKSENSE_DB` on a local, non-synchronized disk for live use; avoid concurrent OneDrive synchronization of an active SQLite database. Do not commit databases, logs, passwords, or `.env` files.

```sh
docker build -t stocksense .
docker run --rm -p 8000:8000 -v stocksense-data:/app/data stocksense
```

SQLite and full-state polling keep a small installation simple. For larger catalogs or multiple application replicas, migrate persistence to PostgreSQL, introduce paginated API queries and database migrations, replace process-local throttling with shared limits, and use an application server plus background email delivery. The inventory transaction boundary and UI/API separation provide the starting structure for that work. This version does not claim horizontally scalable or high-volume production readiness.

The supplied Excalidraw link was inaccessible during implementation; the interface follows the written requirements.
