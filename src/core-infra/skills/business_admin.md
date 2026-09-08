# Skill: Versa - Business Admin

> **Trigger:** Load when the Primary User (or setup / FEATURE AVAILABILITY) asks you to **install, operate, style, or implement against the HTTP API** of Versa - Business Admin (VBA).
> **Scope:** COA only (`coa_only`) — never deployed to sub-agents.
> **Feature gate:** Only when FEATURE AVAILABILITY does **not** say Versa - Business Admin is OFF. Ask the PU before install/configure. Do not deploy until they agree.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

Skill id is **`business_admin`**. **COA-only** (`coa_only`) — not for sub-agents. Product name is **Versa - Business Admin** (short **VBA**). Shipped directory: **`Versa-BusinessAdmin`**. Official production repo: **`versa-business-admin`**.

There is **one Primary User per Versa AGi system**. Do not write procedures for a second PU.

This is **not** agitop. agitop is **Versa AGi - Mission Control** (host operator console). Do not load this skill for agitop work.

## What this skill is for

Load this skill when the Primary User (or setup) asks you to **install, operate, style, or implement against the HTTP API** of Versa - Business Admin (VBA).

Humans read the User Manual in the product repo. This skill is the COA procedure: **detect whether VBA is already installed on this host** → then install or continue from the existing instance.

**Ask the Primary User before install or configure. Do not deploy until they agree.**

## Sources of truth (read before acting)

| Need | Source |
|---|---|
| User / operator manual (setup, roles, maintenance, upgrades) | `docs/ops/BUSINESS_ADMIN_OPS_MANUAL.md` in the repo |
| Product README (name, roles, skill+manual usage) | `README.md` |
| HTTP API catalog (this version) | `GET /api` (open) and Settings → API (`/settings?tab=api`) |
| Living API contract | `docs/production/state/state_api_contract.md` |
| Locked upgrade design D1–D6 | `docs/production/state/state_upgradability.md` |
| Feature map | `docs/production/state/shape_business_admin.md` |
| Public production repo (HTTPS) | `https://github.com/swartzlib7/versa-business-admin` |
| SSH remote (when keys already work) | `git@github.com:swartzlib7/versa-business-admin.git` |
| Integration line | `beta` |
| Production line | `master` |

Never contradict the manual or D1–D6. Amend the manual rather than creating parallel ops guides.

## What this skill covers (summary)

1. **Orient — already installed?** Decide install vs continue (mandatory first step).
2. **Enablement** — setup seeds the reserved Project when the feature is ON. COA does not register a second name.
3. **Clone / Install** — only if Orient said not installed **and** the Primary User agreed.
4. **Read** — User Manual §2 (setup) and §1.5 (Admin vs member).
5. **Implement the API** — discover via `GET /api`; do not share the host AGi database.
6. **Operate** — restart recipe, backups, seed-only upgrades (D1–D6), migrate when tasked.

## Orient (do this first, every time)

Do **not** clone or run a greenfield install until you know the answer.

**Installed on this host** when any of these hold:

| Check | How |
|---|---|
| Project | `agictl project list` shows a project **named** `Versa-BusinessAdmin`. Numeric ids differ on every install — never key off an id from another host. |
| Workspace | Directory `Versa-BusinessAdmin` exists under the agent workspace, with `package.json`. That is always the shipped directory. |
| Running | `curl -s localhost:<port>/api/health` returns `status=ok` (review often **:3200**) |

Then:

| Finding | Continue with |
|---|---|
| Project + workspace present (health may be down) | **Already installed.** Do not clone a second copy. Operate, implement, or repair from that workspace. Restart per manual §3.5 if health is down. |
| Project exists, directory missing | Ask the Primary User. If they agree, clone into the **existing** project workspace. Do not register a second project. |
| No project named `Versa-BusinessAdmin` | **Stop.** Tell the Primary User. Setup seeds that reserved Project when `[features] business_admin` is ON. Do **not** `agictl project add` under any other name. |
| Workspace present, no Project | **Stop.** Same as above — do not register a second name. |

Review `localhost:<port>` is not production. Production is whatever host the Primary User names.

## Enablement

Versa AGi **setup** seeds the reserved Project **`Versa-BusinessAdmin`** when `[features] business_admin` is ON. That Project’s description carries the public GitHub URL and the install instructions (clone `beta`, read the User Manual, run §2, implement against `GET /api`).

COA does **not** run `agictl project add versa-business-admin` (or any other name). If Orient found no project, stop and report — do not create a duplicate.

## Install (only if Orient said not installed, or directory missing)

Ask the Primary User first. Do not clone, configure, or deploy until they agree.

1. Clone if needed (HTTPS is the setup URL; SSH is fine when keys already work):
   ```bash
   git clone https://github.com/swartzlib7/versa-business-admin.git Versa-BusinessAdmin
   # or, if SSH keys already work:
   # git clone git@github.com:swartzlib7/versa-business-admin.git Versa-BusinessAdmin
   cd Versa-BusinessAdmin
   git checkout beta
   npm ci
   cp .env.example .env.local   # edit locally — never commit secrets
   ```
2. Read `docs/ops/BUSINESS_ADMIN_OPS_MANUAL.md` §2 (and §3.5 before any restart).
3. Postgres is required (`DATA_SOURCE=postgres` + `DATABASE_URL` in `.env.local`, manual §2.4); `DATA_SOURCE=fixture` is opt-in.
4. Boot (review often port **3200** — use the port the PU named):
   ```bash
   npm run build
   # kill the exact PID from: ss -tlnp | grep 3200
   # never pkill -f — it self-matches
   nohup ./node_modules/.bin/next start -p 3200 > __tmp/next3200.log 2>&1 &
   ```
5. Verify in a **separate** call (root `/` may stall — do not relaunch on timeout):
   ```bash
   curl -s localhost:3200/api/health
   # expect status=ok and version matching package.json
   ```
6. First-boot checklist: manual §2.6 (login, hard-refresh). Install accounts: Administrator human `admin@example.com`, COA agent `coa@example.com`. Change both passwords on first login. Other people are sample data. Demo mode shows the install-account hint box.

## Implement the API

VBA is a standalone product. Talk to it over HTTP (or Script Tasks), never by sharing the host Versa AGi SQLite.

1. `GET /api` — version, `docs` links, full `resources[]` (method, path, auth, summary). Name: **Versa - Business Admin API**.
2. Operator view of the same catalog: Settings → **API**.
3. Conventions: JSON; list envelope `{ data, count }`; errors `{ error: { code, message } }`.
4. Public routes are open. Mutations need a session cookie; writes are admin unless noted (`admin-or-self`, `admin-or-assignee`). Same login form for Admin and member — difference is `role` after login (manual §1.5).
5. Catalog custom fields use the `c_` namespace. Sample rows use `ba_sample:` via Settings → Modes — Demo never swaps the live backend.
6. Public System Landscape path: `GET /api/public/system-landscape`. List agents with `GET /api/users?type=agent`. Do not add compatibility aliases before v1.0.0.
7. Do not invent `/api/roles*`, page-builder, or host-fleet endpoints.

## Locked upgrade posture (D1–D6 — do not violate)

- Three layers: system code / tenant config (durable catalog overlay) / tenant data.
- Upgrades are **seed-only**: system seed ∪ tenant overlay on boot; system fields hide-not-delete.
- Tenant customizations use the `c_` namespace; overlay rows are Primary-Org-scoped; `overlay.seed_pack` stamps the seed generation.
- Never ship a `db:seed` that truncates tenant data; never clone tenant data as seed.
- Agent packages install/uninstall via the D5 API (0.7.132+); sample data via Settings → Modes.

## Operate (already installed, or after first-boot)

1. Health: `curl -s localhost:<port>/api/health` in its own call.
2. Restart: manual §3.5 — exact PID from `ss -tlnp`, rebuild when switching SHAs, verify health separately.
3. Backups: manual §3.7 (`.data/catalog.json` or `catalog_overlay` + zone tables).
4. Stale UI: rebuild + restart `next start`; hard-reload the browser (`docs/ops/STALE_UI_AND_DEPLOY.md`).
5. First install on a new host (Orient said not installed, PU agreed): manual §2.8 — provision → deploy artifact → env + empty durable store → migrations + **system seed** → first admin human + COA agent → smoke → change both passwords.
6. Host Organization migrate: `scripts/migrate_agi_org.mjs` requires `--primary-source-org-id` to apply; extra own Wave businesses become Orgs (not Branch); it copies credential configuration (never prints it). Do **not** disable the built-in AGi Org module unless the Primary User has asked, after VBA is the system of record. The script **refuses** `--disable-host-org` unless the PU has asked. If both sides have production data, the agent team must merge after apply.
7. Production packaging (container/systemd) is not authored yet — do not invent runbooks; track manual §8.

## Boundaries

- No `master` promotion, no version or dependency bumps without the PU's explicit ask.
- Commit-before-task on `beta`; scoped eslint + `tsc --noEmit` + build before claiming done (manual §4).
- If a PU decision is missing, hold and ask — VBA work is tight-deliverable-control by standing instruction.
- Compatibility aliases / deprecation fallbacks: not until **v1.0.0** when the PU asks.
- Ignore the internal development tree `versa-admin-system` — that is not this shipped project.
