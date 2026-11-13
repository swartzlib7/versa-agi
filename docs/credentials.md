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

## Web search

Search needs no key. Two optional keys add official search APIs inside the local SearXNG, for when the public search pages block it.

| `setup.ini` `[search]` | Service | Effect |
|---|---|---|
| `brave_api_key` | Brave Search API | Adds Brave's API. Brave's public page is no longer read |
| `exa_api_key` | Exa | Adds Exa's API |
| `contact_url` | — (not a key) | A URL or `mailto:` address sent to Wikipedia and Wikidata, which ask every client to say who it is. Blank leaves those two off |

Each service has a free monthly credit; its pricing page says what happens after that.

Setup does not ask for these keys. Set one in any of three ways:

| Who | How |
|---|---|
| You, in the dashboard | agitop → **API Keys** → **Brave Search API Key** or **Exa API Key** → **Save Changes** |
| You, on the command line | `sudo agictl system set-key brave_search <key>` · `sudo agictl system set-key exa <key>` |
| COA | Send COA the key and ask it to turn on better search. It runs `agictl search set-key` and does not keep or repeat the key |

- Saving a key refreshes SearXNG. It can take a minute or two.
- To remove a key, enter `off` in its place.
- `agictl search status` shows whether each key is set (never the key) and which engines answer a test search.
- `contact_url` is edited in `setup.ini`, then `sudo versa-agi-update`.

The keys are kept in `setup.ini` `[search]` and written to `/etc/searxng/settings.yml`.

## Related

- [Models](models.md)
- [Troubleshooting](troubleshooting.md)
