# Skill: Versa - Business Admin — Operate

> **Trigger:** Load when the Primary User or COA asks you to **orient, call the HTTP API, restart, or apply D1–D6** on an already-seeded Versa - Business Admin (VBA) project.
> **Scope:** All agents (`scope=all`) when FEATURE AVAILABILITY does **not** say Versa - Business Admin is OFF.
> **Not this skill:** Clone, install, `project add`, enablement, or first-boot configure. Those stay on COA skill **`business_admin`**.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

Skill id is **`business_admin_operate`**. Product name is **Versa - Business Admin** (short **VBA**). Shipped directory: **`Versa-BusinessAdmin`**. Official production repo: **`versa-business-admin`**.

This is **not** agitop. agitop is **Versa AGi - Mission Control**. Do not load this skill for agitop work.

Do **not** clone, install, or run `agictl project add`. If Orient says the project or workspace is missing, stop and tell COA. COA uses **`business_admin`**.

## Sources of truth (read before acting)

| Need | Source |
|---|---|
| Product README | `README.md` in the workspace |
| Agent door (clone) | `AGENTS.md` in the workspace |
| Ops manual (restart, health, backups) | `docs/ops/BUSINESS_ADMIN_OPS_MANUAL.md` |
| Forms, listings, Spatial Twin, stale UI | `docs/ops/WORKING_WITH_VBA.md` |
| HTTP API catalog (this version) | `GET /api` (open) and Settings → API (`/settings?tab=api`) |
| Upgrade rules D1–D6 | Ops Manual §5 |
| Public production repo (HTTPS) | `https://github.com/swartzlib7/versa-business-admin` |

Never contradict the manual or D1–D6.

## Orient (do this first, every time)

**Installed / ready to operate** when any of these hold:

| Check | How |
|---|---|
| Project | `agictl project list` shows a project **named** `Versa-BusinessAdmin`. Numeric ids differ on every install — never key off an id from another host. |
| Workspace | Directory `Versa-BusinessAdmin` exists under the assigned workspace, with `package.json`. |
| Running | `curl -s localhost:<port>/api/health` returns `status=ok` (review often **:3200**) |

Then:

| Finding | Continue with |
|---|---|
| Project + workspace present (health may be down) | **Operate from that workspace.** Restart per manual §3.5 if health is down. |
| Project exists, directory missing | **Stop.** Tell COA. Do not clone. |
| No project named `Versa-BusinessAdmin` | **Stop.** Tell COA. Setup seeds that reserved Project when `[features] business_admin` is ON. |
| Workspace present, no Project | **Stop.** Same as above. |

Review `localhost:<port>` is not production. Production is whatever host the Primary User names.

## Implement the API

VBA is a standalone product. Talk to it over HTTP (or Script Tasks), never by sharing the host Versa AGi SQLite.

1. `GET /api` — version, `docs` links, full `resources[]` (method, path, auth, summary). Name: **Versa - Business Admin API**.
2. Operator view of the same catalog: Settings → **API**.
3. Conventions: JSON; list envelope `{ data, count }`; errors `{ error: { code, message } }`.
4. Public routes are open. Mutations need a session cookie; writes are admin unless noted (`admin-or-self`, `admin-or-assignee`). Same login form for Admin and member — difference is `role` after login (Ops Manual §1 / README).
5. Catalog custom fields use the `c_` namespace. Sample rows use `ba_sample:` via Settings → Modes — Demo never swaps the live backend.
6. Public System Landscape path: `GET /api/public/system-landscape`. List agents with `GET /api/users?type=agent`. Do not add compatibility aliases before v1.0.0.
7. Do not invent `/api/roles*`, page-builder, or host-fleet endpoints.

## Locked upgrade posture (D1–D6 — do not violate)

- Three layers: system code / tenant config (durable catalog overlay) / tenant data.
- Upgrades are **seed-only**: system seed ∪ tenant overlay on boot; system fields hide-not-delete.
- Tenant customizations use the `c_` namespace; overlay rows are Primary-Org-scoped; `overlay.seed_pack` stamps the seed generation.
- Never ship a `db:seed` that truncates tenant data; never clone tenant data as seed.
- Agent packages install/uninstall via the D5 API (0.7.132+); sample data via Settings → Modes.

## First run

A new install ships in **demo mode**. Turn it off in Settings → Modes before real use. Turning it off deletes the sample pack (`ba_sample:` rows). The Primary Org and the install accounts stay.

## Operate (already installed)

1. Health: `curl -s localhost:<port>/api/health` in its own call.
2. Restart: manual §3.5 — exact PID from `ss -tlnp`, rebuild when switching SHAs, verify health separately.
3. Backups: manual §3.7 (`.data/catalog.json` or `catalog_overlay` + zone tables).
4. Stale UI / local enhance: `docs/ops/WORKING_WITH_VBA.md` (forms, listings, Spatial Twin, rebuild + restart).

## Boundaries

- No clone, no `npm ci` greenfield, no `agictl project add`, no first-boot password change unless COA tasked you after **`business_admin`**.
- No `main` promotion, no version or dependency bumps without the PU's explicit ask.
- If a PU decision is missing, hold and ask COA.
- Compatibility aliases / deprecation fallbacks: not until **v1.0.0** when the PU asks.
- Ignore the internal development tree `versa-admin-system` — that is not this shipped project.
