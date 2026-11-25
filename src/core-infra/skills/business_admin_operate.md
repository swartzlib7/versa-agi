# Skill: Versa - Business Admin — Operate

> **Trigger:** Load when the Primary User or COA asks you to **orient, call the HTTP API, restart, apply D1–D6, or design a change** (a brief, a spec, a new capability) on an already-seeded Versa - Business Admin (VBA) project.
> **Scope:** All agents (`scope=all`) when FEATURE AVAILABILITY does **not** say Versa - Business Admin is OFF.
> **Not this skill:** Clone, install, `project add`, enablement, or first-boot configure. Those stay on COA skill **`business_admin`**.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

Skill id is **`business_admin_operate`**. Product name is **Versa - Business Admin** (short **VBA**). Shipped directory: **`Versa-BusinessAdmin`**. Official production repo: **`versa-business-admin`**.

This is **not** agitop. agitop is **Versa AGi - Mission Control**. Do not load this skill for agitop work.

Do **not** clone, install, or run `agictl project add`. If Orient says the project or workspace is missing, stop and tell COA. COA uses **`business_admin`**.

This checkout is pull-only. Do not commit product changes and do not `git push`. The Primary User publishes `main`.

## Sources of truth (read before acting)

| Need | Source |
|---|---|
| Product README | `README.md` in the workspace |
| Agent door (clone) | `AGENTS.md` in the workspace |
| Ops manual (restart, health, backups, upgrades) | `docs/ops/BUSINESS_ADMIN_OPS_MANUAL.md` |
| Forms, listings, Spatial Twin, stale UI | `docs/ops/WORKING_WITH_VBA.md` |
| **Doing work over the API** (sign-in from a script, records and lines, job costing, attachments, statistics) | `docs/agents/README.md` — recipes that ship with each VBA version |
| What staff see (statistics, assets, mileage, jobs) | `docs/ops/BUSINESS_ADMIN_USER_MANUAL.md` |
| What changed per version | `docs/ops/RELEASE_NOTES.md` |
| **Designing a change** (a brief, a spec, a new capability) | `docs/agents/extending.md`; then, where the checkout has it, the state document for the area in `docs/production/state/` (the public checkout does not have that folder) |
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
4. Public routes are open. Mutations need a session cookie; writes are admin unless noted (`admin-or-self`, `admin-or-assignee`). Same login form for Admin and member — difference is `role` after login (Ops Manual §1 / README). A script gets the cookie by solving the login proof-of-work challenge: `docs/agents/sign-in.md`. Scripts that post statistic values (`/api/statistics/<id>/lines`) use an **access token** (`Authorization: Bearer vba_…`, VBA 1.0.20; HTTP Basic was removed and answers 401). Integrations between VBA installs (push or pull, filters, tokens): `docs/agents/integrations.md`. Never print or store a token outside its Credential; it is shown once. **Using a stored secret** (a vendor key, a mailbox password) in your script: you must be on that Credential's **Users** list; sign in as yourself (your VBA email and password come from your environment; never write them into a script), fetch it each run with `GET /api/credentials/<id>/secret`, and keep it in memory only. Not on the list (`403 NOT_ON_USERS_LIST`): ask the Primary User to add you; do not ask another agent to fetch it for you. Recipe: `docs/agents/integrations.md` (VBA 1.0.20).
5. Catalog custom fields use the `c_` namespace. Sample rows use `ba_sample:` via Settings → Modes — Demo never swaps the live backend.
6. Public System Landscape path: `GET /api/public/system-landscape`. List agents with `GET /api/users?type=agent`. No compatibility aliases unless the PU asks.
7. Do not invent `/api/roles*`, page-builder, or host-fleet endpoints.
8. **Calculated fields are VBA's (1.0.14+).** Every records write goes through VBA's save pipeline, which works out Summary, Formula and allocation fields (e.g. a bill's Allocated / Remaining) and refreshes statistics fed from records. Never send those values yourself; read them back from the response. Calculated defaults (e.g. a line's Unit price) follow their formula unless you send a different value on purpose. A `400` carries a plain sentence in `error.message` (e.g. an over-allocated bill): report it, don't retry blindly.
9. **Integrated records** (an External ID, e.g. from Wave) belong to their integration: don't change their amounts and don't convert them (`/convert` refuses). Clone is allowed and clears the External ID.
10. Add lines one at a time with `POST /api/records/<id>/lines` instead of resending the whole list. Recipes: `docs/agents/records-and-lines.md`, `job-costing.md`, `attachments.md`, `statistics.md`.

## Design a change (brief, spec, new capability)

When the Primary User asks you to plan or design something for VBA, your output is a proposal. This checkout is pull-only: do not change its code, and do not try ideas on the live database.

1. Read `docs/agents/extending.md` in the workspace. It ships with the installed version.
2. Look for `docs/production/state/docs_manifest.md`. The public checkout does not have that folder; a development checkout does. If it is there, read the state document for the area: **decisions in VBA's state documents are senior to your brief**, and where your plan disagrees with one, say so and ask. If it is not there, the decisions in `docs/agents/extending.md` and the code itself are what you have: say in your brief that the state documents were not available to you, and do not cite one you have not opened.
3. Use what exists before adding: look for a catalog record type that fits before proposing a table, and plug into the save pipeline, not the records store.
4. Every code fact you state has a path you opened. Count, do not estimate.
5. Write the brief in the state-document shape (why, decisions, what exists, design, stages with acceptance checks, open decisions with your recommendation) and name the state document it belongs in.

## Locked upgrade posture (D1–D6 — do not violate)

- Three layers: system code / tenant config (durable catalog overlay) / tenant data.
- Upgrades are **seed-only**: system seed ∪ tenant overlay on boot; system fields hide-not-delete.
- Tenant customizations use the `c_` namespace; overlay rows are Primary-Org-scoped; `overlay.seed_pack` stamps the seed generation.
- Never ship a `db:seed` that truncates tenant data; never clone tenant data as seed.
- Agent packages install/uninstall via the D5 API (0.7.132+); sample data via Settings → Modes.

## First run

A new install ships in **demo mode**. Turn it off in Settings → Modes before real use. Turning it off deletes the sample pack (`ba_sample:` rows). The Primary Org and the install accounts stay.

The first boot also installs the site pack (`ba_site:` rows): the Versa AGi Primary slides, the Analysis canvas, styles, and menus. Demo off never removes them. The whole site is in Postgres, so a database backup carries it.

## Operate (already installed)

1. Health: `curl -s localhost:<port>/api/health` in its own call.
2. Restart: manual §3.5 — exact PID from `ss -tlnp`, rebuild when switching SHAs, verify health separately.
3. Upgrade: manual §5.2. Check the installed version first (`/api/health`). **Every upgrade runs `npm run db:migrate`**, even when a version adds no migration. Skipping versions: upgrade straight to the newest version in one go (one `db:migrate` applies every missing migration; the first start brings the newest catalog). **Before that first start, do the "Before the first start" step of every skipped version** (from VBA 1.0.17 or older: set `VBA_DATA_KEY`, Ops Manual §5.2.15, and tell the Primary User to keep a copy of it apart from the server and its backups). Then read each skipped version's §5.2.x notes, oldest first. Don't deploy the versions in between. Leave `.data/site-settings.json` in place; do not restore the development database onto it.
4. Backups: manual §3.7 — `npm run backup` in the workspace writes the database dump, settings and the uploads folder to `backups/` (`--no-uploads` or `VBA_BACKUP_UPLOADS=false` to skip uploads). Uploaded files (pictures, attachments) live on disk in the uploads folder (`VBA_UPLOADS_DIR`, default `.data`), **not** in Postgres: a database dump alone misses them. Back up before every upgrade.
5. Stale UI / local enhance: `docs/ops/WORKING_WITH_VBA.md` (forms, listings, Spatial Twin, rebuild + restart).

## Boundaries

- No clone, no `npm ci` greenfield, no `agictl project add`, no first-boot password change unless COA tasked you after **`business_admin`**.
- No `main` promotion, no version or dependency bumps without the PU's explicit ask.
- If a PU decision is missing, hold and ask COA.
- Compatibility aliases / deprecation fallbacks: only when the PU asks.
- Ignore the internal development tree `versa-admin-system` — that is not this shipped project.
