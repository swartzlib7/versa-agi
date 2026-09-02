# Credentials

Keys can be rotated after install without a full re-provision. The everyday surface is agitop **API Keys**. First-login COA Setup uses the same modal with one primary button (**Set COA model**) that saves typed keys then assigns the model — see [Models](models.md).

## CLI

```bash
sudo agictl system set-key gemini <new-api-key>
sudo agictl system set-key versavoice <new-token>
sudo agictl system set-key xai <new-api-key>
sudo agictl system set-key openai <new-api-key>
sudo agictl system set-key anthropic <new-api-key>
sudo agictl system set-key openrouter <new-api-key>
```

`set-key` enables the provider in `[providers_site]`, migrates, and live-imports shipped catalog keys still missing for that provider.

Re-running `setup.sh` after editing `setup.ini` also rotates credentials.

## Google (Gemini / Vertex)

Google’s API key sits with every other provider key in `setup.ini` `[third_party] google_api_key`. Vertex details (`auth_method`, `project`, `location`, `service_account_key`) live in `[gcp]`.

| Method | INI | Env | Best for |
|--------|-----|-----|----------|
| Gemini API key | `[gcp] auth_method=api_key` + `[third_party] google_api_key` | `GEMINI_API_KEY` (`coa.env`) | Typical |
| Vertex service account | `[gcp] auth_method=vertex` + service account path | `GOOGLE_APPLICATION_CREDENTIALS` | Headless |
| Vertex ADC | `[gcp] auth_method=vertex` | gcloud credentials | Dev |

Vertex harness consume is still **TD-VERTEX-024** — setup can write Vertex paths; the LangGraph client today uses `GEMINI_API_KEY` for Google.

## Other cloud providers

xAI, OpenAI, Anthropic, and OpenRouter keys live in `provider_keys.env` (and the matching `setup.ini` `[third_party] {slug}_api_key`). Membership is **not** a `{slug}_models` CSV — that list is gone. Catalog membership is [shipped / Import](models.md).

A unified `agictl vault` is **TD-SEC-015** (Future). Until then, treat those files as the live store.

## Related

- [Models](models.md)
- [Troubleshooting](troubleshooting.md)
