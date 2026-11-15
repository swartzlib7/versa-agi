# Models

Operator page for catalog layers, first-login COA assign, and Import. Local add/activate is summarized here; the full local-models Owner Manual chapter is assembled later (**TD-OWNER-MANUAL-001**). This page is a seed for that book, not the book itself.

## First login (follow this order)

1. **Accept the VersaVoice connection request** in the mobile app. The welcome message needs that accepted connection.
2. Open **agitop** (`sudo agitop`). First login opens **COA Setup**. Pick a Recommended model and press **Set COA model** (that button also saves any typed keys).
3. Wait for the first pulse (CRON) or run `sudo /home/watchdog/core-infra/lifeline.sh --force`.

Until a catalog model is assigned, COA is held (`invalid_config`, empty model). It stays protected; Lifeline does not spawn or auto-freeze overdue work. Clearing the COA model re-applies the hold.

Stock system default (`[system] model=` / `VERSA_DEFAULT_MODEL`) is **blank** until this assign. There is no `coa_approved_models` list.

## Catalog layers

| Layer | Where | On `--update` |
|-------|-------|----------------|
| **Shipped selection** | `models.ini` `[shipped_models]` | Replaced. Ordered offerings (label + per-provider catalog keys). Not a COA list. |
| **System presets** | `[provider_library]`, `[catalog_library]`, `[model_params]` | Replaced. |
| **Generated live** | `[providers]`, `[catalog]` | Rebuilt by migrate. Do not edit as source. |
| **Site overlays** | `[providers_site]`, `[catalog_selected]`, `[catalog_custom]`, sparse overrides, `[catalog_removed]` | Preserved. |

Remote class is **`cloud`**. Google is an ordinary provider. Extra / utility remotes live in `MODEL_DRIVERS` only until the site Imports them.

## Shipped vs Import vs COA flag

- **Shipped / first-login picker** = `[shipped_models]` ∩ live catalog ∩ catalog `coa` for the keyed provider. Empty catalog → empty picker.
- **Known Import** (a library key) adds the key to `[catalog_selected]` only.
- **Unknown Import** writes a full `[catalog_custom]` row.
- Neither Import path writes generic `[model_params_custom]`.
- **COA eligibility** is the live catalog `coa` flag only.

Current shipped offerings (picker order): Opus 5.5, Grok 4.7, Grok 4.6, Gemini 4 Carbon, Gemini 3.7 Flash, GPT-6.1 Sol, GPT-6 Sol, GPT-5.6 Terra, Opus 4.8, Fable 5.2, Gemini 4 Hydrogen, GPT-5.6 Sol, GLM 5.3 Flash, DeepSeek V4Flash0731, GPT-5.6 Luna. COA Setup suggests the first offering the chosen provider has: Opus 5.5 for Anthropic and for OpenRouter, Grok 4.7 for xAI, Gemini 4 Carbon for Google, GPT-6.1 Sol for OpenAI. Dual-provider offerings import both keys when both providers are enabled. Grok 4.6 and Grok 4.7 default to high reasoning (`low` / `medium` / `high` / `xhigh`) and image ingest is ◆ on both native xAI and OpenRouter keys.

**GLM 5.3 Flash** (`z-ai/glm-5.3-flash`, OpenRouter) is native multimodal: image and video ingest (◆). Reasoning cannot be turned off — stock default is `max` (`low` / `high` / `max`). Use `agictl_view_image` / `agictl_view_video` (mp4, mkv, mov, 200 MB max).

**Gemini 3.7 Flash** is two catalog offerings. Image ingest is ◆ on both. Video ingest is ◆ on native Google (`gemini-3.7-flash`, inline ~20 MB) and on OpenRouter (`google/gemini-3.7-flash`, same `video_url` path as GLM, 200 MB VIEW gate). Declared audio input stays ◇. Use `agictl_view_image` / `agictl_view_video`.

**Claude Opus 5.5, Claude Fable 5.2, Gemini 4 Hydrogen and Gemini 4 Carbon** are each two catalog offerings: the provider's own API and OpenRouter. Image ingest is ◆ on all eight keys. Audio and video on the Gemini 4 models stay ◇.

| Model | Own API key | OpenRouter key | Reasoning levels (stock default) |
|---|---|---|---|
| Claude Fable 5.2 | `claude-fable-5-2` | `anthropic/claude-fable-5.2` | low, medium, **high**, xhigh, max |
| Claude Opus 5.5 | `claude-opus-5-5` | `anthropic/claude-opus-5.5` | low, medium, **high**, xhigh, max |
| Gemini 4 Hydrogen | `gemini-4-hydrogen` | `google/gemini-4-hydrogen` | low, medium, **high**; xhigh through OpenRouter only |
| Gemini 4 Carbon | `gemini-4-carbon` | `google/gemini-4-carbon` | low, medium, **high** |

- **Reasoning cannot be turned off** on these four. An agent set to `none` runs at the model's default level.
- **Fable 5.2 needs 30-day data retention at the provider.** On Anthropic's own API that is an account setting. Through OpenRouter, an account whose privacy setting allows only zero-retention endpoints gets a 404 (see [troubleshooting](troubleshooting.md#a-model-is-refused-through-openrouter-404-zdr)).
- **Fable 5.2 is not offered to model routing** in stock (it is the most expensive model). Model Manager → Edit Model → Router eligible changes that.
- A reply on these models is streamed, so a long reply is not stopped by the stall timer while it is still arriving.

**GPT-6.1 Sol** and **GPT-6 Sol** are each two catalog offerings: `gpt-6.1-sol` / `gpt-6-sol` (OpenAI key) and `openai/gpt-6.1-sol` / `openai/gpt-6-sol` (OpenRouter). GPT-6.1 Sol always reasons: low, medium, **high**, xhigh, max. GPT-6 Sol adds none and can have reasoning turned off. Image ingest is ◆ on all four keys. Replies are streamed.

**GPT-5.6 and later with an OpenAI key** are called on OpenAI's Responses endpoint, the only one that takes tools and reasoning together. Nothing is stored at OpenAI: each reply carries its reasoning record, encrypted, and the conversation sends it back. Before 3.9.0 the three GPT-5.6 models failed with an OpenAI key at every reasoning level except none.

**Changing an agent's model keeps its conversation.** A reply holds parts only its own provider reads (reasoning records, the provider's search calls and results). When the conversation continues on another provider, the reply goes as its text and tool calls, and a provider search becomes one line naming the pages it returned.

## Provider web search

A model can search the web itself, using its provider's search. It is a setting on the model, **on for every model that has a search route**, and the provider bills each search on top of tokens.

- To turn it off: agitop → **Models** → select the model → **Edit** → **Web search** → off. The field is shown only for a model that has a search route, with a notice that each search is charged.
- Or `sudo agictl model params set model:<catalog_key> --web-search off` (`on` to turn it back on).
- Through OpenRouter, an account that allows only zero-retention endpoints is refused some models when they search. Versa AGi then asks once more without the search and leaves it off for that cycle; the cycle log says `WEB SEARCH: off for this cycle`.

| Route | Models | Who searches |
|---|---|---|
| OpenRouter | Gemini and Claude models (`google/…`, `anthropic/…`). Not GPT: use the OpenAI key of the same model | Google Search for Gemini, Anthropic's web search for Claude |
| Anthropic's own API | Claude Opus 4.6 and later, Fable | Anthropic's web search, at most 5 searches a request |
| OpenAI's own API | GPT-5.6 and later, while the reasoning level is not none | OpenAI's web search. The three GPT-5.6 models ship with reasoning at none, so they search once you raise it |
| Google's own API | — | Not available: use the OpenRouter key of the same model |

With the setting on, the agent is asked to search in a step of its own and to state what it found. The pages a reply used are listed under it as **Sources** (through OpenRouter) and named in the cycle log as `WEB SEARCH: n source(s)`. Sources for Gemini are Google redirect links. The per-search charge is not included in agitop's cost totals. This is separate from the agents' own search (`agictl search`), which every model can use.

## Cached input

Each step of a cycle sends the agent's prompt and conversation again. Providers bill that repeated input at a lower, cached price. Versa AGi sets this up for you; there is nothing to configure.

| Provider | How | Price of repeated input |
|---|---|---|
| Claude, with an Anthropic key or through OpenRouter | Versa AGi marks each request for the cache (new in 3.9.0) | About 5% of the input price on Opus 5.5, 2.5% on Fable 5.2, 10% on Opus 4.8. Writing to the cache costs 125%, once |
| OpenAI, Google, xAI, and the other models through OpenRouter | The provider does it by itself | Set by the provider |

- A Claude cache entry lasts 5 minutes from its last use. Steps of a cycle are closer together than that.
- **While a chat waits for your next message, the entry is kept alive.** Every 4 minutes the system sends the last request again and asks for no reply. That costs a cached read (about 1 cent for a 50,000-token conversation on Opus 5.5) and saves writing everything again (about 25 cents). The cycle log has a `CACHE KEEP-ALIVE: ping` line for each one.
- A tool run or a live call longer than 5 minutes is not covered yet: the request after it pays the full price once, and the one after that is cached again.
- The cycle log ends with a `CACHE:` line: how many input tokens were read from the cache and how many were written. The cycle's cached tokens and its cost estimate use the same numbers.

## Assign and Import

```bash
sudo agictl model catalog list --table
sudo agictl agent set-model coa <catalog_key>
sudo agictl agent set-model charlie <catalog_key>
sudo agictl agent set-model charlie --clear   # inherit system default
sudo agictl model params set model:<catalog_key> --reasoning-effort high
```

Model Manager (agitop) is the same surface: Import, `coa` tick, params. After a provider key is saved, setup/migrate live-imports shipped keys still missing for that provider.

## Local models (pointer)

**What is supported:** [Local AI support](local-ai-support.md) (Ollama vs SYCL vs sd-cli). Design detail: [System Design §4.5.0](../design/Versa%20AGi%20-%20System%20Design.md).

Weights live only on the GPU host (`topology=local` or `server`). A `topology=client` host never downloads weights — import on the server, then `sudo agictl model refresh`.

```bash
agictl model hf inspect 'hf://…/….gguf'
sudo agictl model sycl import 'hf://…/….gguf' --name gemma4:e4b --runtime chat
sudo agictl model activate gemma4:e4b
```

Inspect first. Chat GGUFs go to SYCL / Ollama. Media bundles are a separate Provider (`local_media`) — Qwen-Image and Flux paint. LTX-2.5 Distilled video is **experimental** (parked; usable 720p-class needs **>64 GB VRAM**). Local vision (`mmproj`) is chat image-in on the SYCL keys that already have it (`qwen3.6:35b`, `qwen3.8:27b`).

## Related

- [Local AI support](local-ai-support.md) — Ollama vs SYCL vs sd-cli
- [Credentials](credentials.md) — keys and `agictl system set-key`
- [Operations](operations.md) — agitop
- [Troubleshooting](troubleshooting.md)
