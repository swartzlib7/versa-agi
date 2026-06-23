# Utility Models — One-Shot Generation

> **Purpose**: Run catalog models as **Utility Models (UM)** — single-purpose profiles with a fixed system prompt and artifact output. Distinct from a Normal Agent work cycle.
> **Scope**: All agents (`all`)

> **Harness tools:** Examples use shell form (`agictl utility …`). In a work cycle, call tool **`agictl_utility`** and pass only the part **after** `agictl` as the `command` argument.

## Mental model

| Concept | What it is |
|---------|------------|
| **Normal Agent** | LangGraph cycle — tools, memory, tasks, conversation |
| **Utility Model** | `utility_models` row — catalog key + system prompt + output path/modality |
| **Utility Task** | `task_kind=utility` + `utility_model_id` FK — lifeline runs UM as **`assigned_to`** on due date |
| **Manual run** | `agictl utility run <id>` — uses UM `run_as_agent` unless `--task-id` overrides |

Use a UM when you need a **deterministic one-shot** (image, audio, text artifact) without spinning a full reasoning cycle. Use a Normal Agent when the work needs judgment, tools, or multi-step coordination.

## Listing profiles

```bash
agictl utility model list
agictl utility model show brand-hero-square
```

**agitop:** System Settings → **Utility Models** tab — New / Edit / Delete profiles.

When **Utility Models** is disabled in System Settings, the Task modal **Utility** tab is disabled and lifeline skips `utility run-due-tasks`. Active utility tasks are **frozen** automatically (prior status saved in `pre_freeze_status`).

## Manual run

```bash
agictl utility run weekly-summary \
  --input-files reports/q1.pdf,notes.txt \
  --vars '{"topic":"Q1 review"}'

agictl utility run brand-hero-square --dry-run
```

| Flag | Meaning |
|------|---------|
| `--input-files` | Comma-separated paths; validated against catalog mime map |
| `--output-dir` | Override UM default output path |
| `--vars` | JSON for `{{var}}` substitution in system prompt |
| `--task-id` | Link to Utility Task; runs as task `assigned_to` |

Artifacts land under the run context agent home (default `.agent/utility`). A `manifest.json` is written when enabled in `setup.ini [utility_models]`.

## Utility Tasks

Create via CLI or agitop Task modal → **Utility Task** tab:

```bash
agictl task add "Weekly hero" --assignee coa --due-date "2026-06-20 09:00:00" \
  --utility-task --utility-model brand-hero-square \
  --utility-input-files '["brand/ref.jpg"]' \
  --utility-start-alert --utility-stop-alert \
  --utility-spawn-agent coa
```

| Callback | Behavior |
|----------|----------|
| **Start Alert** | Short VersaVoice message to PU when run begins |
| **Stop Alert** | VV message on success or failure |
| **Spawn agent** | On success, lifeline wakes named agent with artifact paths in prompt |

Lifeline executes due Utility Tasks **before** normal spawn logic. Task status → `done` on success, `blocked` on failure.

## Reading artifacts

After a run, inspect paths from the JSON response or `manifest.json`:

- **Text** — `view` / read file in workspace
- **Image** — `agictl_view_image` with artifact path
- **Audio / video** — `listen` (when wired) or report path to PU

## Mime validation errors

Input files must match `catalog_modality_maps` for the UM's `catalog_model`. The merged **`file`** modality covers PDF, DOCX, CSV, TXT, etc.

```bash
agictl model modality-map show gemini-2.5-flash
```

Common errors: `input_invalid` (bad extension), `output_mismatch` (UM output modality not in catalog), `driver_pending` (image/audio/video driver not yet wired — use text UM for now).

## When to escalate to COA / PU

- UM profile missing or disabled → PU adds via agitop Utility Models tab
- Repeated `blocked` Utility Tasks → PU reviews task + input files
- Need new catalog key or mime map → PU via Model Manager

## Cross-links

- Model assignments and routing: `agent_model_management.md`
- Task lifecycle: `task_scheduling.md`
- Full operator CLI: `cli_reference.md` (PU / COA admin)
