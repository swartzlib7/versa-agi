#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Organization Database Initialization
#
# Creates the single organization.db schema (Wave integration).
# Location: /var/lib/versa-agi/organization.db
# Access:   watchdog (Owner) / coa (Group Read/Write), mode 660
#           Shared by COA (sync) and STEWART (business operations).
#
# Implements Organization plan deliverables:
#   D23  Single-file placement (all tables here — SQLite FKs cannot span files)
#   D1   Money as INTEGER cents (*_cents); line quantity as REAL
#   D2   STRICT tables; datetimes/dates as ISO-8601 TEXT; bools as INTEGER 0/1
#   D4   WAL journal mode + synchronous=NORMAL (persistent, set once here)
#   D6   Writer-independent updated_at via AFTER UPDATE triggers
#   D7   Core + bridging tables with declared, enforced foreign keys
#   D8   Exchange integration tracker + its three indexes
#
# Soft references: connection_uid points at the native `connections` table in
# tasks.db (a DIFFERENT file), so it is a plain TEXT column with NO foreign key
# — cross-database FKs are impossible in SQLite. All in-file relationships use
# real, enforced foreign keys.
# ─────────────────────────────────────────────────────

set -euo pipefail

DB_PATH="${1:-/var/lib/versa-agi/organization.db}"

echo "Initializing organization database: ${DB_PATH}"

sqlite3 "${DB_PATH}" <<'SQL'
-- ── Persistent file properties (D4) — set once, survive reconnect ──
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

-- ═══════════════════════════════════════════════════════
-- Core business layer
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS organizations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  slug        TEXT UNIQUE,
  type        TEXT,
  notes       TEXT,
  logo_path   TEXT,
  external_id TEXT,
  is_active   INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Credentials — agentic access tokens for email/API/MCP (D35)
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS credentials (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  auth_type     TEXT NOT NULL,                     -- managed vocab: IMAP, MCP, API …
  configuration TEXT NOT NULL DEFAULT '{}',         -- JSON config based on type
  notes         TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

CREATE TABLE IF NOT EXISTS email_addresses (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE,
  label         TEXT,
  is_primary    INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
  usage_notes   TEXT,
  credential_id INTEGER REFERENCES credentials(id)
) STRICT;

CREATE TABLE IF NOT EXISTS physical_addresses (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  line_1      TEXT,
  line_2      TEXT,
  city        TEXT,
  state       TEXT,
  postal_code TEXT,
  country     TEXT,
  label       TEXT,
  is_primary  INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1))
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Picklists — universal managed-vocabulary lookup
--
-- One row = one selectable option for a (table_name, field_name) target. Drives
-- the agitop form picklists for open vocabularies (organizations.type,
-- products.type, invoices.status, estimates.status, transactions.category) and
-- the shared currency list. ``table_name=''`` means the option applies to that
-- field on ANY table (used for currency, which appears on several tables).
-- ``name`` is the display label; ``value`` is what gets written to the target
-- column. No foreign keys — it references columns by NAME (a soft, config-style
-- reference), so it stays decoupled from the data tables it serves.
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS picklists (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,                       -- display label
  value      TEXT NOT NULL,                       -- stored value
  table_name TEXT NOT NULL DEFAULT '',            -- target table ('' = any table)
  field_name TEXT NOT NULL,                       -- target column
  position   INTEGER NOT NULL DEFAULT 0,          -- sort order within the list
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Relationship bridging
--
-- Customer/Vendor relationships are NOT stored — they are DYNAMIC, implied by
-- the documents two orgs exchange: when Org A issues an invoice/estimate to
-- Org B, A is a vendor to B and B is a customer to A (see the derived customer/
-- vendor views in organization_reader). Only people-to-org membership is a
-- stored bridge: an org_staff row means "this connection belongs to this org"
-- (no type — the row's existence is the whole fact).
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS org_staff (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id         INTEGER NOT NULL,
  connection_uid TEXT,                       -- soft ref → tasks.db connections(uid)
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Products
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS products (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          INTEGER NOT NULL,
  name            TEXT NOT NULL,
  description     TEXT,
  type            TEXT,
  sku             TEXT,
  unit_price_cents INTEGER,                  -- money: integer minor units (D1)
  currency        TEXT,
  is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
  external_id     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id) REFERENCES organizations(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Invoices
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS invoices (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id          INTEGER NOT NULL,                        -- issuing org (the vendor)
  customer_org_id INTEGER,                                 -- billed org (the customer)
  invoice_number  TEXT,
  status          TEXT,                      -- Wave-controlled vocab (draft/sent/viewed/overdue/paid…)
  subtotal_cents  INTEGER,                   -- money (D1)
  tax_total_cents INTEGER,                   -- money (D1)
  total_cents     INTEGER,                   -- money (D1)
  currency        TEXT,
  issue_date      TEXT,                      -- ISO-8601 date 'YYYY-MM-DD'
  due_date        TEXT,
  paid_date       TEXT,
  notes           TEXT,
  external_id     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id)          REFERENCES organizations(id),
  FOREIGN KEY (customer_org_id) REFERENCES organizations(id)
) STRICT;

CREATE TABLE IF NOT EXISTS invoice_line_items (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id      INTEGER NOT NULL,
  product_id      INTEGER,
  description     TEXT,
  quantity        REAL,                      -- fractional units allowed (D1)
  unit_price_cents INTEGER,                  -- money (D1)
  total_cents     INTEGER,                   -- money: round(quantity * unit_price_cents) (D1)
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (invoice_id) REFERENCES invoices(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Estimates
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS estimates (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id                 INTEGER NOT NULL,                 -- issuing org (the vendor)
  customer_org_id        INTEGER,                          -- quoted org (the customer)
  estimate_number        TEXT,
  status                 TEXT,
  subtotal_cents         INTEGER,            -- money (D1)
  tax_total_cents        INTEGER,            -- money (D1)
  total_cents            INTEGER,            -- money (D1)
  currency               TEXT,
  issue_date             TEXT,               -- ISO-8601 date
  expiry_date            TEXT,
  notes                  TEXT,
  external_id            TEXT,
  converted_to_invoice_id INTEGER,
  created_at             TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at             TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id)                  REFERENCES organizations(id),
  FOREIGN KEY (customer_org_id)         REFERENCES organizations(id),
  FOREIGN KEY (converted_to_invoice_id) REFERENCES invoices(id)
) STRICT;

CREATE TABLE IF NOT EXISTS estimate_line_items (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  estimate_id     INTEGER NOT NULL,
  product_id      INTEGER,
  description     TEXT,
  quantity        REAL,                      -- fractional units allowed (D1)
  unit_price_cents INTEGER,                  -- money (D1)
  total_cents     INTEGER,                   -- money (D1)
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (estimate_id) REFERENCES estimates(id),
  FOREIGN KEY (product_id)  REFERENCES products(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Transactions
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS transactions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id           INTEGER NOT NULL,                        -- whose books this is
  counterparty_org_id INTEGER,                              -- the other party (vendor/customer), nullable
  account_name     TEXT,
  transaction_date TEXT,                     -- ISO-8601 date
  description      TEXT,
  amount_cents     INTEGER,                  -- money (D1)
  currency         TEXT,
  category         TEXT,
  external_id      TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (org_id)             REFERENCES organizations(id),
  FOREIGN KEY (counterparty_org_id) REFERENCES organizations(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Exchange — generic integration tracker (D8)
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS exchange (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,                              -- system: 'Wave', 'Versa AGI'…
  source_table  TEXT NOT NULL,                              -- e.g. 'invoices'
  source_id     INTEGER,                                    -- PK in source_table
  external_id   TEXT,                                       -- id in the external system
  source_org_id INTEGER REFERENCES organizations(id),       -- org owning the source side
  target_org_id INTEGER REFERENCES organizations(id),       -- org owning the target side
  origin        TEXT CHECK(origin IN ('agent', 'user', 'integration')),
  status        TEXT NOT NULL DEFAULT 'new'
                  CHECK(status IN ('new', 'sync-done', 'sync-failed')),
  replicate     INTEGER NOT NULL DEFAULT 0 CHECK(replicate IN (0, 1)),  -- transient push signal
  error_message TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK(source_org_id IS NULL OR target_org_id IS NULL
        OR source_org_id != target_org_id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Email bridging
--
-- A customer/vendor is just an organization, so its email lives in org_emails
-- like any org's. org_staff_addresses (below) carries a staff person's address.
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS org_emails (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id   INTEGER NOT NULL,
  email_id INTEGER NOT NULL,
  FOREIGN KEY (org_id)   REFERENCES organizations(id),
  FOREIGN KEY (email_id) REFERENCES email_addresses(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Address bridging
--
-- org_addresses: any organization's address (incl. orgs that act as a customer
-- or vendor). org_staff_addresses: a staff person's own address.
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS org_addresses (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id     INTEGER NOT NULL,
  address_id INTEGER NOT NULL,
  FOREIGN KEY (org_id)     REFERENCES organizations(id),
  FOREIGN KEY (address_id) REFERENCES physical_addresses(id)
) STRICT;

CREATE TABLE IF NOT EXISTS org_staff_addresses (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  org_staff_id INTEGER NOT NULL,
  address_id   INTEGER NOT NULL,
  FOREIGN KEY (org_staff_id) REFERENCES org_staff(id),
  FOREIGN KEY (address_id)   REFERENCES physical_addresses(id)
) STRICT;

-- ═══════════════════════════════════════════════════════
-- Indexes
-- ═══════════════════════════════════════════════════════

-- Exchange — the three indexes from the plan (§2)
CREATE INDEX IF NOT EXISTS idx_exchange_lookup   ON exchange(name, source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_exchange_pending  ON exchange(replicate, status);
CREATE INDEX IF NOT EXISTS idx_exchange_external ON exchange(external_id);

-- Picklists — fast lookup per field/table + a uniqueness guard (one option per
-- field/table/value so the same option can't be added twice).
CREATE INDEX IF NOT EXISTS idx_picklists_lookup ON picklists(table_name, field_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_picklists_unique
  ON picklists(field_name, table_name, value);

-- external_id — sync upsert match keys (UNIQUE — one external record per local
-- row; NULLs are exempt so locally-created records without an external_id are
-- unaffected). Exchange.external_id is intentionally NOT unique here — the same
-- external system ID can appear for different source_table entities.
--
-- Migration: if a pre-existing non-unique index occupies the name, DROP it first
-- so the UNIQUE version can be created (IF NOT EXISTS skips when the name exists
-- regardless of uniqueness).
DROP INDEX IF EXISTS idx_organizations_external;
DROP INDEX IF EXISTS idx_products_external;
DROP INDEX IF EXISTS idx_invoices_external;
DROP INDEX IF EXISTS idx_estimates_external;
DROP INDEX IF EXISTS idx_transactions_external;

CREATE UNIQUE INDEX idx_organizations_external ON organizations(external_id);
CREATE UNIQUE INDEX idx_products_external     ON products(external_id);
CREATE UNIQUE INDEX idx_invoices_external     ON invoices(external_id);
CREATE UNIQUE INDEX idx_estimates_external    ON estimates(external_id);
CREATE UNIQUE INDEX idx_transactions_external ON transactions(external_id);

-- Foreign-key columns — join/filter performance
CREATE INDEX IF NOT EXISTS idx_products_org             ON products(org_id);
CREATE INDEX IF NOT EXISTS idx_invoices_org             ON invoices(org_id);
CREATE INDEX IF NOT EXISTS idx_invoices_customer        ON invoices(customer_org_id);
CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice    ON invoice_line_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_items_product    ON invoice_line_items(product_id);
CREATE INDEX IF NOT EXISTS idx_estimates_org            ON estimates(org_id);
CREATE INDEX IF NOT EXISTS idx_estimates_customer       ON estimates(customer_org_id);
CREATE INDEX IF NOT EXISTS idx_estimate_items_estimate  ON estimate_line_items(estimate_id);
CREATE INDEX IF NOT EXISTS idx_estimate_items_product   ON estimate_line_items(product_id);
CREATE INDEX IF NOT EXISTS idx_transactions_org         ON transactions(org_id);
CREATE INDEX IF NOT EXISTS idx_transactions_counterparty ON transactions(counterparty_org_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date        ON transactions(transaction_date);

-- ═══════════════════════════════════════════════════════
-- updated_at triggers (D6) — writer-independent freshness.
-- The WHEN guard lets an explicit caller-supplied updated_at win; otherwise
-- any UPDATE refreshes it. Recursive triggers are OFF by default, so the
-- inner UPDATE does not re-fire.
-- ═══════════════════════════════════════════════════════

CREATE TRIGGER IF NOT EXISTS trg_organizations_updated_at
AFTER UPDATE ON organizations FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE organizations SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_products_updated_at
AFTER UPDATE ON products FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE products SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_updated_at
AFTER UPDATE ON invoices FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE invoices SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_estimates_updated_at
AFTER UPDATE ON estimates FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE estimates SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_transactions_updated_at
AFTER UPDATE ON transactions FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE transactions SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_exchange_updated_at
AFTER UPDATE ON exchange FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE exchange SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_credentials_updated_at
AFTER UPDATE ON credentials FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
  UPDATE credentials SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ═══════════════════════════════════════════════════════
-- Picklist seed — shipped default vocabularies.
-- Inserted ONLY when the table is empty (a fresh DB), so operator edits made via
-- "Manage Lists" / agictl survive a later setup --update (which re-runs this
-- script). table_name='' marks a list that applies to a field on ANY table
-- (the shared currency list).
-- ═══════════════════════════════════════════════════════

INSERT INTO picklists (name, value, table_name, field_name, position)
SELECT column1, column2, column3, column4, column5 FROM (
  VALUES
    ('Business',     'business',     'organizations', 'type', 1),
    ('Individual',   'individual',   'organizations', 'type', 2),
    ('Non-Profit',   'non-profit',   'organizations', 'type', 3),
    ('Government',    'government',   'organizations', 'type', 4),
    ('Other',        'other',        'organizations', 'type', 5),
    ('Service',      'service',      'products', 'type', 1),
    ('Product',      'product',      'products', 'type', 2),
    ('Subscription', 'subscription', 'products', 'type', 3),
    ('Expense',      'expense',      'products', 'type', 4),
    ('Draft',        'draft',        'invoices', 'status', 1),
    ('Sent',         'sent',         'invoices', 'status', 2),
    ('Viewed',       'viewed',       'invoices', 'status', 3),
    ('Partial',      'partial',      'invoices', 'status', 4),
    ('Overdue',      'overdue',      'invoices', 'status', 5),
    ('Paid',         'paid',         'invoices', 'status', 6),
    ('Draft',        'draft',        'estimates', 'status', 1),
    ('Sent',         'sent',         'estimates', 'status', 2),
    ('Viewed',       'viewed',       'estimates', 'status', 3),
    ('Accepted',     'accepted',     'estimates', 'status', 4),
    ('Declined',     'declined',     'estimates', 'status', 5),
    ('Expired',      'expired',      'estimates', 'status', 6),
    ('Invoiced',     'invoiced',     'estimates', 'status', 7),
    ('Income',       'income',       'transactions', 'category', 1),
    ('Software',     'software',     'transactions', 'category', 2),
    ('Hosting',      'hosting',      'transactions', 'category', 3),
    ('Office',       'office',       'transactions', 'category', 4),
    ('Travel',       'travel',       'transactions', 'category', 5),
    ('Fees',         'fees',         'transactions', 'category', 6),
    ('Other',        'other',        'transactions', 'category', 7),
    ('USD', 'USD', '', 'currency', 1),
    ('EUR', 'EUR', '', 'currency', 2),
    ('GBP', 'GBP', '', 'currency', 3),
    ('ZAR', 'ZAR', '', 'currency', 4),
    ('CAD', 'CAD', '', 'currency', 5),
    ('IMAP', 'imap', 'credentials', 'auth_type', 1),
    ('MCP',  'mcp',  'credentials', 'auth_type', 2),
    ('API',  'api',  'credentials', 'auth_type', 3)
)
WHERE NOT EXISTS (SELECT 1 FROM picklists);
SQL

echo "Organization database initialized: ${DB_PATH}"
echo "Standards: single-file (D23), cents money + REAL quantity (D1), STRICT (D2), WAL (D4), updated_at triggers (D6)"
