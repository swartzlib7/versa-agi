# Skill: Organization — Business records & accounting domain

> **Trigger**: Use when working with organizations, customers/vendors (as orgs), products, invoices, estimates, transactions, exchange/sync status, or financial summaries in the Organization domain.
> **Scope**: COA (sync ownership) and Accountant / agents assigned Organization work. Only when FEATURE AVAILABILITY does **not** say Organization is OFF.

> **Harness tools:** Examples use shell form (`agictl organization …`). In a work cycle, call **`agictl_organization`** (registered when Organization is ON) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Mental model

| Concept | Meaning |
|---------|---------|
| **Organization** | A party (your company, customer, or vendor) — relationships emerge from invoices/estimates/transactions |
| **Money** | Always **integer cents** (e.g. `$45.00` → `4500`) |
| **Exchange** | Integration sync queue/status — inspect errors; COA runs sync |
| **Accountant ops** | Invoice/expense/product authoring + reporting |
| **COA sync** | Integration / exchange execution — not the Accountant's job |

## Common commands

```bash
agictl organization org list
agictl organization org add --name "Acme LLC" --notes "customer"
agictl organization org get <id>

agictl organization product list
agictl organization invoice list
agictl organization invoice add --customer-org-id <id> --status draft
agictl organization invoice-item add --invoice-id <id> --product-id <id> --quantity 1 --unit-price-cents 4500

agictl organization transaction list
agictl organization estimate list
agictl organization exchange list
agictl organization exchange get <id>
```

Entity actions are consistent: `add` / `get` / `list` / `update` / `upsert` (and `delete` where allowed). Discover flags with `agictl organization <entity> add --help`.

## Rules of engagement

1. **Feature gate** — If spawn context says Organization is OFF, do not use this command group.
2. **Cents only** — Never pass dollar floats for money fields.
3. **Sync boundary** — Accountant: fix data quality and ops; escalate sync/credential/exchange failures to COA.
4. **Demo data** — `organization demo seed|clear|status` is for empty/demo datasets only; never clear a store that holds real records.
5. **Deeper CLI** — Full surface: **cli_reference.md** (`organization` group) on demand.
