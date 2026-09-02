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

Current shipped offerings (picker order): Grok 4.6, Gemini 3.7 Flash, GPT-5.6 Terra, Opus 4.8, GPT-5.6 Sol, GLM 5.3 Flash, DeepSeek V4Flash0731, GPT-5.6 Luna. Dual-provider offerings import both keys when both providers are enabled.

**GLM 5.3 Flash** (`z-ai/glm-5.3-flash`, OpenRouter) is native multimodal: image and video ingest (◆). Reasoning cannot be turned off — stock default is `max` (`low` / `high` / `max`). Use `agictl_view_image` / `agictl_view_video` (mp4, mkv, mov, 200 MB max).

**Gemini 3.7 Flash** is two catalog offerings. Image ingest is ◆ on both. Video ingest is ◆ on native Google (`gemini-3.7-flash`, inline ~20 MB) and on OpenRouter (`google/gemini-3.7-flash`, same `video_url` path as GLM, 200 MB VIEW gate). Declared audio input stays ◇. Use `agictl_view_image` / `agictl_view_video`.

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

Weights live only on the GPU host (`topology=local` or `server`). A `topology=client` host never downloads weights — import on the server, then `sudo agictl model refresh`.

```bash
agictl model hf inspect 'hf://…/….gguf'
sudo agictl model sycl import 'hf://…/….gguf' --name gemma4:e4b --runtime chat
sudo agictl model activate gemma4:e4b
```

Inspect first. Chat GGUFs go to SYCL / Ollama. Media bundles are a separate Provider (`local_media`). Local vision (`mmproj`) is chat image-in on the SYCL keys that already have it (`qwen3.6:35b`, `qwen3.8:27b`).

## Related

- [Credentials](credentials.md) — keys and `agictl system set-key`
- [Operations](operations.md) — agitop
- [Troubleshooting](troubleshooting.md)
