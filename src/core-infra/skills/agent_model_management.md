# Agent Model Management

> **Purpose**: Manage persistent agent model assignments, ephemeral routing, PU model feedback, and model lookback via cycles.
> **Scope**: COA only (`coa_only`)

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_task`, `agictl_cycle`, …) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument. Full map: **cli_reference_agent.md** (*Harness tool invocation*).

## Concepts

- **Persistent assignment** — `agents.db.model` via `agictl agent set-model`. Survives spawns.
- **Ephemeral routing** — per-spawn triage may select a different execution model. Does **not** call `set-model`. Recorded on `cycles.db` via `agictl cycle get`.
- **Work modality** — cognitive tier: `fast`, `balanced`, `reasoning`, `code`, `local` (catalog `work_modality`).
- **I/O modalities** — catalog `input_modalities` / `output_modalities` (text, image, audio, video).

## When to change persistent vs rely on routing

| Situation | Action |
|-----------|--------|
| PU wants an agent to default to a specific model always | `agictl agent set-model <agent> <catalog_key>` |
| PU wants automatic per-task model selection | Enable **Auto model routing** on the agent **General** tab (Agent modal) |
| One-off quality issue on a routed spawn | Log PU feedback; do not change assignment unless PU asks |

## PU model feedback

When the PU complains about model quality for a task type:

```bash
# PU prefers a model for debugging work
agictl model feedback add --key deepseek/deepseek-v4-flash --preference prefer \
  --work-modality code --task-hint debugging --note "PU prefers for debugging"

# PU wants to avoid a model for architecture reviews
agictl model feedback add --key gemini-2.5-flash-lite --preference avoid \
  --work-modality reasoning --task-hint "architecture review"
```

List feedback: `agictl model feedback list --table`

Update fields: `agictl model feedback update <id> [--preference] [--work-modality] [--task-hint] [--note]`

**Delete** (hard remove from `agents.db`): `agictl model feedback remove <id>`

**agitop:** Model Manager → **Model Feedback** — catalog key picklist, two-column form, bordered records table. **Delete** permanently removes the selected row (no soft-deactivate). Duplicate `catalog_key + preference + work_modality` combinations are rejected on add.

## Model lookback (which model produced a reply?)

1. Find the message: `agictl message get <uid> --contact <sender>`
2. Note `cycle_id` on the message row
3. Inspect the cycle: `agictl cycle get <cycle_id>`
4. Read `execution_model`, `routing_mode`, `routing_work_modality`, `assigned_model`

## COA model assignment rules

- Only COA-approved catalog keys may be assigned to COA (`coa_approved=true`).
- Sub-agents may use any enabled catalog key (subject to provider availability).

```bash
agictl agent set-model coa gemini-3-flash-preview
agictl agent set-model researcher deepseek/deepseek-v4-flash
agictl model catalog list --table
```

## System routing preferences

PU configures preferred models per work modality in **🔀 ROUTING** (`[model_routing]` in setup.ini).

| Mode | Behavior |
|------|----------|
| **Pool** | Triage classifies tier and may pick `recommended_model` from router-eligible candidates (assigned model excluded). PU **Model Feedback** `prefer`/`avoid` entries bias the choice. |
| **Preferred** | Triage classifies tier only; harness maps tier → your single preferred key for that tier (e.g. `code=deepseek/...`). No candidate list in triage. |

### Work modality vs model class (common confusion)

| Term | Meaning |
|------|---------|
| **`class: local`** | Ollama / llama.cpp deployment (on-prem inference) |
| **`work_modality: local`** | Triage tier = privacy / on-prem preference |
| **`work_modality: code`** | Triage tier = implementation / debugging |

**Pool mode** matches candidates whose catalog `work_modality` equals the triage tier. A local Ollama model tagged `work_modality=local` is **not** auto-selected for `code` tasks — even if it is good at coding.

**Preferred mode** uses the key you set on the Work tab for that tier, regardless of the model's catalog tag.

**Practical local routing:**

1. Enable **Router eligible** on the catalog row (Model Manager → Edit Model).
2. **Preferred mode:** set the tier slot directly (e.g. `code → qwen3.6:35b`).
3. **Pool mode:** set the model's catalog **Work modality** to match the tier you want (e.g. `code` for a local coding model).
4. Local models are not COA-approved by default — they appear in routing picklists but **COA spawns ignore them** at runtime; sub-agents can use them.

COA routing guard: delegation / task-for-another-agent → `balanced` or `reasoning`, not `code`. Direct COA implementation (edit file, patch, "I'll fix…") still routes to `code` when enabled.

## Generation parameters (mental model)

Three layers — **config stays data; translation stays code**:

```
models.ini JSON  →  normalized params  →  to_native_kwargs()  →  LangChain / API request
     (data)              (stable)              (adapter code)
```

| Layer | Where | What you edit |
|-------|--------|----------------|
| **Data** | `[model_params]`, `[model_params_custom]`, agent Overrides tab | JSON per `default` or `model:<catalog_key>`; agents.db nullable columns |
| **Stable** | `resolve_model_params()` in `harness/model_params.py` | Merged vocabulary: `temperature`, `reasoning_effort`, `reasoning_max_tokens`, `allowed_reasoning_efforts`, `think_mode`, `extra` |
| **Adapter** | `to_native_kwargs()` same module | Maps stable keys → provider-native kwargs (`ChatAnthropic`, `ChatOpenAI`, `ChatOllama`, …) |

**Precedence:** agent override → `model:<key>` → `default` → system defaults.

**Passthrough:** sampling knobs (`top_p`, `top_k`, penalties, …) go in the **`extra`** JSON bag unless promoted to a normalized field (`agictl model params set --extra` or Model Manager **Extra passthrough**).

**Per catalog key, not per provider slug:** `model:claude-opus-4-8` (direct Anthropic) and `anthropic/claude-opus-4-8` (OpenRouter) are separate keys with separate param rows and different adapter families.

**Cycle debug:** `MODEL PARAMS (triage|execution/…)` lines show catalog-layer values; `native=` shows post-translation kwargs.

```bash
agictl model params get model:claude-opus-4-8
agictl model params set model:qwen3.6:35b --reasoning-effort high --extra '{"top_p":0.95}'
```

## Local parameters (Ollama + SYCL)

Ollama stores **default sampling** in the model manifest `params` blob (`temperature`, `top_p`, `top_k`, penalties). Versa AGi does **not** read that blob — configure equivalents in `[model_params]` / `[model_params_custom]`.

### Two local providers (one catalog row per model)

Local inference uses honest provider slugs in `[catalog]`:

| Provider | Inference stack (`gpu_backend`) | LangChain client | `think_mode` | Sampling from `extra` |
|----------|--------------------------------|------------------|--------------|------------------------|
| **`ollama`** | `standard` | `ChatOllama` | `reasoning_effort` → Ollama `think` | full Ollama set (`top_k`, penalties, `num_ctx`, …) |
| **`llamacpp`** | `intel` | `ChatOpenAI` → llama-server | **not configurable** | `temperature`, `top_p`; `num_predict` → `max_tokens` |

`gpu_backend` describes the **inference stack** — on-box (`topology=local|server`) or on the **remote server** when `topology=client`. It is not the same as `VERSA_GPU_BACKEND=remote` in `paths.env`, which only means “client topology, inference elsewhere.” A client pointing at a remote Ollama host (`gpu_backend=standard`, port 11434) still uses provider `ollama`.

`agictl model migrate` assigns the provider from setup.ini `gpu_backend`. Only the active provider is enabled in `[providers]` baseline. Routing and the harness branch on the catalog slug; `paths.env` supplies URL/auth only.

**Thinking** on Ollama uses API field `think` ([docs](https://docs.ollama.com/capabilities/thinking)), not the params blob. Declare `think_mode` in the param layer:

| `think_mode` | Models | Mapping |
|--------------|--------|---------|
| **`boolean`** | Qwen 3, DeepSeek R1 | `none` → off; `low`/`medium`/`high` → on (same intensity) |
| **`levels`** | GPT-OSS on Ollama | `low` / `medium` / `high` passed through |

On **SYCL/llamacpp**, the dashboard hides thinking options for `think_mode` models (template may still reason; no `think` API).

```ini
model:qwen3.6:35b = {"reasoning_effort":"none","think_mode":"boolean","allowed_reasoning_efforts":["none","low","medium","high"],"extra":{"top_p":0.95,"top_k":20,"repeat_penalty":1,"presence_penalty":1.5}}
```

```bash
agictl model params set model:qwen3.6:35b --think-mode boolean \
  --allowed-reasoning-efforts none,low,medium,high \
  --extra '{"top_p":0.95,"top_k":20}'
```

**Vision:** set `input_modalities=text,image` on the catalog row. In-spawn vision uses `agictl_view_image` when the execution model supports image input.

**Debugging provider routing:** cycle logs include `LLM ROUTE (triage|execution/…)` lines with `catalog_provider`, `client`, `api_model`, and `native=` params — use when verifying local `llamacpp`/`ollama` or cloud routes during spawn tests.

## Output routing (generation — Phase F)

`[output_routing]` maps **one preferred catalog key per output delivery modality** (`image`, `audio`, `video`). Configure in **🔀 ROUTING** (lower section). Models must declare the matching `output_modalities` in the catalog.

Consumed by Utility Model runners / output drivers (not chat spawn triage). Resolver: `resolve_output_model()` in `harness/model_routing.py`. See TD-UTIL-001 (Production Plan §2.6).

## Cost estimates (TD-COST-001 partial)

OpenRouter list rates live in `models.ini` `[catalog_pricing]` (USD per million tokens). Refreshed on every `setup.sh` / `--update` via `agictl model openrouter patch-template`, which resolves **all non-local catalog keys** to OpenRouter list prices (Gemini, xAI, OpenAI, Anthropic, and OpenRouter-routed models). Unmatched keys show `—` in Model Manager.

Per-cycle estimated cost (`cost_usd_estimated` on `cycles.db`) and thinking/cached token telemetry are **TD-COST-002** — not yet written at cycle end. When shipped, inspect via `agictl cycle get <id>`. Figures will be **estimates** from token counts × list rates, not provider invoices.

```ini
[output_routing]
image=
audio=
video=
```

## Vision / multimodal

One **execution model per spawn**. The harness does not switch models mid-cycle.

**In-cycle:** when the execution model supports `image` input, call:

```
agictl_view_image(path="/path/to/screenshot.png")
```

Or validate via CLI: `agictl view image <path>`. Cycle logs show `VIEW INJECT` and `VIEW TRIM` lines.

**Cross-spawn fallback:** when the execution model lacks vision:

1. Pick a vision-capable catalog key: `agictl model catalog list --table`
2. Reassign persistently if appropriate: `agictl agent set-model <agent> <vision_key>`
3. Journal progress: `agictl task progress <id> 'DONE: ... NEXT: view screenshot at <path> ...'`
4. Schedule the next wake — snooze an owned task (`agictl task snooze <id> 5`) or create a self-assigned task due now stating the intention (`agictl task add "..." --due-date "YYYY-MM-DD HH:MM:SS"`)
5. End cycle: `agictl cycle end 'Need vision — next cycle on <vision_key>'`
6. Next Lifeline tick spawns on the vision-capable model; call `agictl_view_image(path="...")` (or `agictl view image <path>` to validate).

Do **not** expect a second model inside the same cycle. In-spawn inter-model handover is a separate architecture (not implemented).

## Utility Models (one-shot generation)

For fixed system-prompt + artifact output (not a full agent cycle), use **Utility Models** — see **`utility_models.md`**. Chat catalog keys can back a UM; lifeline runs Utility Tasks as `assigned_to`.

## Catalog fields (for reference)

```
class|provider|enabled|coa_approved|ctx_rec|ctx_max|work_modality|input_modalities|output_modalities|router_eligible|Label
```
