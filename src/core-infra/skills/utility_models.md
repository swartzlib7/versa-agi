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

Local Qwen-Image-2512: read **`local_media_qwen_image_2512.md`** and `agictl model media usage qwen-image-2512` before painting.
Local FLUX.1-dev: read **`local_media_flux1_dev.md`** and `agictl model media usage flux1-dev` before painting.

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

## Long image / audio runs

`agictl_utility` waits **900 seconds** (other `agictl_*` tools stay at 120). Local image paint on a client SSHs to the GPU host and copies the PNG back into the UM output dir — that path needs the longer budget.

If you see `ERROR: Command timed out after 900 seconds` (or older 180/120s wording):

1. **Do not immediately re-run** the same UM. A run lock (`running`) means generation is still in progress; a second start will fail or collide.
2. **Check the UM `output_path`** (and `manifest.json` if present) for a new artifact. If a file appeared, treat the run as succeeded and continue (view / send / journal).
3. If nothing is there yet: journal the expected path, **`agictl task snooze <id> 5`** (minimum 5 minutes), and `agictl cycle end`. On the next wake, inspect the directory again before starting a new run.
4. Re-run only after the lock is gone **and** no new artifact exists.

`--utility-spawn-agent` (or a follow-up Normal Agent task) is the right way to quality-check an image after it lands — do not burn the current cycle waiting in a tight poll loop.

## Utility Tasks

Create via CLI or agitop Task modal → **Utility Task** tab:

```bash
agictl task add "Weekly hero" --assignee coa --due-date "2026-06-20 09:00:00" \
  --utility-task --utility-model brand-hero-square \
  --utility-input-files '["brand/ref.jpg"]' \
  --utility-start-alert --utility-stop-alert \
  --utility-spawn-agent coa
```

| Flag | Meaning |
|-------|---------|
| `--utility-task` | Sets `task_kind=utility` (mutually exclusive with `--script-task`) |
| `--utility-model` | FK to `utility_models.id` (required) |
| `--utility-input-files` | JSON array of paths (validated against catalog mime map) |
| `--utility-start-alert` | VersaVoice short message to PU when run starts |
| `--utility-stop-alert` | VV message to PU on completion (success or error) |
| `--utility-spawn-agent` | Optional — spawn named agent on success with artifact paths in wake |

Lifeline executes due Utility Tasks **before** normal spawn logic. Task status → `done` on success, `blocked` on failure.

## Reading artifacts

After a run, inspect paths from the JSON response or `manifest.json`:

- **Text** — `view` / read file in workspace
- **Image** — `agictl_view_image` with artifact path
- **Audio / video** — `listen` (when wired) or report path to PU

If you need to **reason about the output** (quality check, describe to PU), do it in a **later Normal Agent spawn** — use `--utility-spawn-agent` or a follow-up task so the artifact paths arrive in that wake prompt.

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
