# GitHub CLI (gh) — issues, pull requests, build results, releases and repository search on GitHub

> **Trigger:** The work needs something on GitHub that plain git cannot do: read or open an issue or a pull request, see why a build failed, publish a release, search repositories, or look through an account's repositories. Also when the Primary User asks you to connect to their GitHub account. Load this skill before you sign in or propose anything.
> **Scope:** All agents (`all`).
> **Rule of order:** git first. Cloning, committing, branching and pushing are `git_operations.md` and need nothing from this skill. `gh` is for the rest.

> **Harness tools:** Examples use shell form. Run `gh` through `agictl_execute` (`bash "…"`). For `agictl` commands, pass only the part after `agictl` to the matching tool. Full map: **cli_reference_agent.md**.

## 0. You do the work

You sign in, run the commands and report. The person does only what only they can do:

| Only the person can | How they do it |
|---|---|
| Decide what you may reach on GitHub, and with which account (§2) | They tell you |
| Create a token in their GitHub account, or approve a sign-in code | In their own browser, at github.com |
| Invite an account of yours to their repositories, if they choose that | In their own browser |

Never hand the person a command to run on this machine.

## 1. Check what is here

```bash
gh --version
gh auth status
```

- `gh` is installed by Versa AGi's setup at `/usr/local/bin/gh`. If the command is missing, say so to the Primary User: the next update of Versa AGi installs it. Do not build your own.
- `gh auth status` says which account you are signed in as, or that you are not signed in. Each agent signs in for itself: a sign-in belongs to your Linux account and no other agent has it.
- Options change between versions. Before a command you have not used here, read its help: `gh <command> <subcommand> --help`.

## 2. Find out what they want first

Ask, in plain words, only what you do not already know:

1. **Which repositories**, and whether it is their personal account or an organisation's.
2. **What you should do there**: read only (issues, pull requests, builds), or also write (open issues and pull requests, comment, publish releases).
3. **As whom.** Two choices, and it is theirs:

| Choice | What it means |
|---|---|
| **A token on their own account** | What you do on GitHub appears as them. Quickest to set up. A fine-grained token can be limited to chosen repositories and actions |
| **An account of your own**, which they invite to the repositories | What you do appears as you, and they can remove you without touching their own account. They create the account, or ask you to, and send the invitation; you still sign in with a token made in that account |

## 3. Sign in

### 3a. With a token (the usual way)

1. **Read the current steps first:** `agictl search read "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens"`. GitHub's settings pages change; that page is the authority, not your memory.
2. **Walk them through it one step per message.** In GitHub: Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → generate a new one, with an end date, limited to the repositories from §2, and only the permissions the work needs:

   | For this work | Repository permission |
   |---|---|
   | Read code, files, releases | Contents: read |
   | Push, or publish a release | Contents: read and write |
   | Issues | Issues: read, or read and write |
   | Pull requests | Pull requests: read, or read and write |
   | Build results (Actions) | Actions: read |

   Check the names against the page you read in step 1. Ask for less, not more: a token can be replaced by a wider one later.
3. **They send you the token. You store it:**

   ```bash
   printf '%s' '<token>' | gh auth login --hostname github.com --with-token
   gh auth status
   ```

   A token GitHub does not accept is refused at once (`Bad credentials`). Then check one thing you were given: `gh repo view <owner>/<repo>`.
4. **The token is a credential.** It belongs only in `gh`'s own store in your home. Once `gh auth status` shows you signed in, ask the person to delete the message they sent it in. Do not copy it into a task, memory, a log, a summary or another message, and do not print it (`gh auth token`, `gh auth status --show-token`).

### 3b. With a code (when they want you to have their whole account)

`gh` can also sign in with a one-time code the person types at GitHub. This gives you the reach of their account, not of a limited token, so use it only when that is what they asked for.

```bash
nohup gh auth login --hostname github.com --git-protocol https --web > ~/.cache/gh-login.txt 2>&1 < /dev/null &
```

```bash
cat ~/.cache/gh-login.txt
```

The file holds a one-time code and the address `https://github.com/login/device`. Send both to the person in one message. The command keeps waiting in the background for a short time; when they have entered the code, `gh auth status` shows you signed in. If the code ran out, start again. Delete `~/.cache/gh-login.txt` afterwards.

## 4. git and gh side by side

- Your projects already push with the key or the token Versa AGi set up for them (`git_operations.md`). `gh` does not change that.
- **Do not run `gh auth setup-git`** on a machine where pushing already works: it changes how git signs in for every repository of yours.
- A repository is still registered as a project by COA (`project_management.md`). Finding a repository with `gh` does not make it a project.

## 5. Common work

Add `--repo <owner>/<repo>` when you are not inside that repository's folder. Your tool returns a limited amount of text, so ask for only the fields you need with `--json <fields>` and `--jq '<filter>'` (the `--help` of each command lists its fields), and `--limit <n>` on lists.

| To | Command |
|---|---|
| List an account's repositories | `gh repo list <owner> --limit 30` |
| Look at one repository | `gh repo view <owner>/<repo>` |
| Search repositories | `gh search repos "<words>" --limit 10` |
| List open issues | `gh issue list --repo <owner>/<repo>` |
| Read an issue with its comments | `gh issue view <number> --comments` |
| Open an issue | `gh issue create --title "<title>" --body "<text>"` |
| Comment on an issue | `gh issue comment <number> --body "<text>"` |
| Open a pull request from your branch | `gh pr create --title "<title>" --body "<text>" --base <branch>` |
| List pull requests | `gh pr list` |
| Read one, and what changed | `gh pr view <number>` · `gh pr diff <number>` |
| See whether its checks passed | `gh pr checks <number>` |
| Review one | `gh pr review <number> --comment --body "<text>"` |
| List recent builds | `gh run list --limit 10` |
| See why a build failed | `gh run view <run-id> --log-failed` |
| Publish a release with files | `gh release create <tag> <file>… --title "<title>" --notes "<text>"` |
| Add a file to a release | `gh release upload <tag> <file>` |
| Anything else GitHub offers | `gh api <path>` (read `gh api --help`) |

A pull request is how your work reaches a shared branch: push your own branch with git, then `gh pr create`, and tell the Primary User or COA it is waiting for review.

## 6. What needs the Primary User's word, each time

Being able to is not being asked to. Do these only when the Primary User asked for exactly that:

- Merge a pull request into the main branch (`gh pr merge`).
- Delete anything: a repository, a branch that is not yours, a release, an issue's comments.
- Change a repository's settings, visibility, secrets, collaborators or protection rules.
- Create a public repository, or make something public.
- Work in a repository that is not part of a project you were given.

And always:

- Nothing from this system's internals goes into an issue, a pull request, a release note or a comment (`security_protocol.md`): those are read by people outside.
- A release or a comment you publish cannot be fully taken back. Show the Primary User the text first when it is their product speaking.

## 7. Signing out

```bash
gh auth logout --hostname github.com
```

Do it when the work that needed GitHub is finished, or when the person asks. Tell them they can also end the token themselves in GitHub's settings, which stops it everywhere at once.
