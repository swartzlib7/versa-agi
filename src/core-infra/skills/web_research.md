# Skill: Web Research — find pages, read them, and say where facts came from

> **Trigger**: Use this skill when you need facts from the web: documentation, version checks, prices, how-to steps, or anything you cannot answer from your memory and files. It tells you which tool to use first and what to do when a search returns nothing.

> **Harness tools:** Examples use shell form (`agictl group …`). In a work cycle, call the matching tool (`agictl_search`, `agictl_browser`) and pass only the part **after** `agictl` as the `command` argument. Never prefix `agictl` in the argument.

## Which tool, in this order

| Step | Command | Use it for |
|---|---|---|
| 1. Find | `agictl search web "<query>"` | Finding pages. Returns title, URL, snippet, engine |
| 2. Read | `agictl search read "<url>"` | Reading one page as text. Fast, no browser. Use it for documentation, articles, references, raw files |
| 3. Browse | `agictl browser goto "<url>"` | Only when step 2 is not enough: the reply has a `hint`, the page needs a click or a form, or you need a screenshot |

- Start at the lowest step that can answer. If you already have the URL, start at step 2.
- A snippet from step 1 is a pointer, not a source. Read the page before you rely on it.
- The browser is slower and may be turned off for you. `search read` only needs web search to be on.
- Do not use `curl` or `wget` through the terminal to read pages. `search read` gives you clean text and paging.

## Find

```bash
agictl search web "<query>"                 # 5 results
agictl search web "<query>" --count 10      # up to 10
agictl search web "<query>" --categories science
```

- Put the exact terms in the query: product name, version, error text in quotes.
- If the results are off, change the query. Sending the same query again returns the same results.

## Read

```bash
agictl search read "<url>"                          # first 8000 characters of the page text
agictl search read "<url>" --offset <next_offset>   # read on from where the last reply stopped
agictl search read "<url>" --max-chars 20000        # more per call (limit 20000)
agictl search read "<url>" --links                  # also list the page's links (up to 100)
```

The reply:

| Field | Meaning |
|---|---|
| `title`, `url` | The page, after any redirect |
| `content` | The text. Headings, paragraphs, list items and table rows each start a line |
| `content_chars` | Length of the whole page text |
| `truncated`, `next_offset` | Present when there is more. Pass `next_offset` as `--offset` to read on |
| `links` | With `--links`: `[{text, url}]`. Use it to move to the next page of a manual |
| `hint` | The page builds its content with scripts. Read it with `browser goto` |

- Read on only while you still need more. Stop when you have the answer.
- A PDF, image or other file is refused with its type. Say so, or look for an HTML version.
- `HTTP 403` or `429` means the site refused the request. Try the browser once, then another source.

## Browse

See the `browser_automation` skill. `browser goto` has the same paging (`--offset`, `--max-chars`).

## When a search returns nothing

A reply with `count: 0` and `unresponsive_engines` means the engines were blocked or slow, not that nothing exists.

1. Run `agictl search status`. It shows which engines answer a test search and why the others did not.
2. If some engines answer, rephrase and search again once.
3. If none answer, do not keep retrying. Use what you can reach directly with `search read` (official documentation sites, a known URL), and tell whoever asked that web search is not answering right now.

## Reporting what you found

- Give the URL for each fact you rely on.
- Say when two sources disagree, and which is more recent or more official.
- Say what you could not find or could not open. Do not fill a gap with a guess.

## COA: better search with an API key

Search works without any key. It reads the public pages of several search engines, and those sites block automated readers from time to time. Two official search APIs can be added. Only do this when the Primary User asks for better or more reliable search, or agrees after you explain it.

| Service | What the Primary User does | You run |
|---|---|---|
| Brave Search API | Creates a key at `https://brave.com/search/api` (free monthly credit; a card is required) | `agictl search set-key brave <key>` |
| Exa | Creates a key at `https://exa.ai` (free monthly credit) | `agictl search set-key exa <key>` |

1. Check first: `agictl search status` shows `api_keys_set` and which engines answer.
2. Tell the Primary User what a key is for, that each service has a free monthly credit and may bill beyond it, and where to create one. They can also enter it themselves in agitop → **API KEYS**.
3. When they send you a key, run `search set-key`. It saves the key and refreshes search; this can take a minute or two.
4. Run `agictl search status` again and tell them the result: the key is set, and `braveapi` or `exaapi` is answering.
5. Do not repeat the key back, and do not store it in memory, awareness, a task, or a file.
6. To remove a key: `agictl search set-key brave off` (or `exa off`).

Sub-agents cannot set keys. If search is failing for you, tell COA.
