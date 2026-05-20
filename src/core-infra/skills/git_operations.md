# Git Operations Skill

## Prerequisites
- **Git identity** (user.name, user.email) and **credential.helper store** are auto-configured during provisioning — see `~/.gitconfig`
- **SSH key** is auto-generated at provisioning — public key at `~/.ssh/versa_agi_ed25519.pub`
- Public key must be added to the Git hosting platform (GitHub, GitLab, etc.) by the Primary User
- Only COA (protected agents) can run `project git-setup` and `project add`

## Authentication Methods

### SSH (default for `git@` URLs)
- Key: `~/.ssh/versa_agi_ed25519`
- Config: `~/.ssh/config` (auto-configured for github.com + gitlab.com)
- Requires PU to add the public key as a deploy key on the Git platform

### Access Token (for `https://` URLs)
- When a project has an `access_token` set (via `agictl project update --access-token`), `project assign` auto-writes the credential to `~/.git-credentials`
- Format: `https://oauth2:{token}@{host}` — git resolves this automatically via `credential.helper store`
- **No manual config needed** — credentials are injected before cloning
- One `.git-credentials` file supports multiple hosts (one line per host)

## SSH Key Delivery (First Git Project)

When assigning an agent to a git project for the **first time**:

1. Read the agent's public key: `cat ~/.ssh/versa_agi_ed25519.pub`
2. Write the key content to a markdown file (for attachment)
3. Send a message to the Primary User with the MD file attached (`--markdown-paths`), requesting they add it as a **deploy key** on the Git platform
4. Create a tracking task: `agictl task add "PU: Add SSH deploy key for {agent} on {platform}"`
5. Snooze the task: `agictl task snooze <id> 5` (check back in 5 minutes)
6. **Do not attempt git push operations until the PU confirms the key is added**

## Workspace & Filesystem

All project work MUST happen inside your `workspace/` directory. Each project has its own subdirectory:

```
~/workspace/<project-name>/    ← Git repos and local projects live here
```

Use your workspace for **everything** — code, builds, temp files, test outputs, scratch work. If you need a temporary directory for builds or testing, create it inside the project workspace:

```bash
mkdir -p workspace/<project-name>/__tmp
```

## Cloning a Repository

Use `agictl project add` to register the project, then clone manually:

```bash
cd workspace/<project-name>/
git clone git@github.com:<owner>/<repo>.git .
```

## Standard Git Operations

All git operations happen INSIDE `workspace/<project>/`:

```bash
cd workspace/<project>/
git status
git add .
git commit -m "description"
git push origin main
git pull origin main
```

## Branch Management

```bash
git checkout -b feature/my-feature    # Create feature branch
git push -u origin feature/my-feature # Push new branch
git checkout main                      # Switch back
git merge feature/my-feature          # Merge when ready
```

## Merging Remote Work

When pulling and merging work from other developers:

```bash
cd workspace/<project>/
git fetch origin                              # Fetch all remote changes
git log --oneline origin/development -n 10    # Review incoming commits
git merge origin/development                  # Merge into your branch
```

If merge conflicts arise:
1. Review conflicts with `git diff`
2. Resolve manually — prefer the remote version unless you have local changes that supersede
3. `git add .` and `git commit -m "Merge: resolve conflicts from development"`
4. Report conflicts and resolutions to the Primary User

## Build & Test Operations

When running builds, tests, or docker operations for a project:

```bash
# ALWAYS work within the project workspace
cd workspace/<project>/

# For Docker builds — use the project directory as build context
docker build -t <project>-test .

# For temporary build artifacts
mkdir -p __tmp/
# Use __tmp/ for logs, outputs, temp data — NEVER /tmp/

# Cleanup after testing
rm -rf __tmp/
```

## Rules

- NEVER run git commands outside `workspace/`
- NEVER use `/tmp/` for any project-related work
- NEVER modify `.agent/poise.md` via git
- Always use descriptive commit messages
- Always pull before pushing to avoid conflicts
- Sub-agents requesting git setup must route through COA
- Create `__tmp/` inside the project for any temporary files
- Keep build artifacts within the project workspace
