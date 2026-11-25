# Contributing to Versa AGi

Thank you for your interest in Versa AGi! This document outlines the contribution process.

## License

Versa AGi is licensed under the [Business Source License 1.1](../LICENSE.md). By contributing, you agree that your contributions will be governed by this license.

## Getting Started

1. **Read the [README](../README.md)** for project overview and installation
2. **Read the [Agent security model](security.md)** and the other pages in `docs/` for how the system is put together
3. **Fork the repository** and create a feature branch

## Development Setup

### Prerequisites

- Linux (Ubuntu 24.04 recommended). On macOS use an Ubuntu 24.04 machine in OrbStack or Lima; on Windows use WSL
- Node.js v22+
- Python 3.12+
- SQLite3, Git, jq, inotify-tools

### Local Development

```bash
# Clone your fork
git clone https://github.com/your-username/versa-agi.git
cd versa-agi

# Run setup (creates OS users, databases, CRON)
sudo ./src/setup.sh

# Deploy changes after edits
sudo ./src/setup.sh --update
```

### Running the tests

One command runs the unit tests on the three Python interpreters the product runs on and judges them together:

```bash
cd src/core-infra
python3 harness/tests/run_tests.py
```

It takes about two minutes and ends with `CLEAN` or a list of problems. The result is clean only when no test fails on any interpreter and every test passes on at least one.

| Interpreter | Path | Has |
|---|---|---|
| harness | `/usr/local/lib/versa-agi/venv` | LangChain and the model provider SDKs |
| agictl | `/opt/versa-agi/venv` | click, rich, textual (agictl and agitop) |
| system | `/usr/bin/python3` | What Lifeline's scripts use |

A test that needs a package its interpreter does not have skips there with a reason, and runs on the interpreter that has it. `--only agictl harness` runs fewer interpreters; `--json FILE` saves every result. To run one file: `python3 -m unittest harness.tests.test_link_service`.

When you add a test module that imports click, rich, textual or LangChain, start it with the same guard the other modules have, so the other interpreters skip it instead of failing to load. Tests use the repository's `src/models.ini` and temporary databases; a test must never read or write this machine's install.

## How to Contribute

### Reporting Issues

- Use GitHub Issues for bugs and feature requests
- Include your OS, Node.js version, and relevant log output
- For security vulnerabilities, email the maintainers directly — do not open a public issue

### Code Contributions

1. **Fork** the repository
2. **Create a branch** from `main` with a descriptive name
3. **Make changes** following the project conventions:
   - Bash scripts: follow existing patterns in `src/core-infra/`
   - Python code: follow existing patterns in `src/core-infra/agictl/` and `src/core-infra/agitop/`
   - Poise/Skills: markdown format, see existing files in `src/core-infra/config/` and `src/core-infra/skills/`
4. **Test** by running `sudo ./src/setup.sh --update` and verifying with `sudo agitop`
5. **Submit a Pull Request** with a clear description of the change

### Security rules for a change

Agents run as their own Linux accounts and reach data only through `agictl`, which runs as the system account (`watchdog`). A change keeps these true. Each has a test in `src/core-infra/harness/tests/test_caller_identity.py` that fails when it is broken.

- **Who is calling.** `agictl` takes its caller from sudo's own record (`caller_identity.py`), before anything else runs. Do not add another way for a caller to say who it is, and do not read a caller's identity from a variable it could set.
- **What the wrapper passes.** A variable the `agictl` wrapper must hand on is named in three places together: the wrapper's loop, the `env_keep` line in `setup.sh`, and the test. Never add `SETENV` to a sudo rule.
- **Root.** The system account reaches root for the commands listed in `caller_identity.ROOT_FOR_WATCHDOG` and no others. A new `sudo -n agictl …` from Lifeline or `agictl` is added to that list. Nothing on it may run text a caller supplies.
- **Databases.** Only the system account writes a database. Agent-side code calls `agictl`; it does not open a database file to write, and a file mode is never loosened to let it.
- **Secrets.** `setup.ini` and the agents' config files are for the system account only. Code that runs in an agent's process reads the cycle's copy of the settings (`AGICTL_SETUP_INI`), which has the secret values blanked. A new secret setting has a key ending in `_key`, `_token`, `_secret` or `_password`, so the copy blanks it.
- **Files for a cycle.** Anything Lifeline writes for a cycle goes in that agent's own data folder (`/var/lib/versa-agi/<agent>/run/`) and is removed when the cycle ends. Nothing an agent is told, and no key, is written under `/tmp`.
- **Groups are given by name.** The system account has a group of its own that no agent is in, and a file it writes takes that group. A file an agent must read is put in a folder that passes on the agents' group (setgid, `agi_agents`), or is given its group in `setup.sh`. Never rely on the group the writing account happens to have, and try the change on a machine installed the normal way: a development machine's accounts can differ.
- **Folders.** An agent's home is closed to the other agents. What agents share goes in the shared workspace. Owners, groups and modes are set in `setup.sh` (`apply_system_permissions`) and checked by `src/audit_permissions.sh`; change both together.

### Agent Roles

Community members can contribute new agent roles by adding a directory to `src/core-infra/config/roles/`:

```
config/roles/your-role/
├── poise.md     # Behavioral framework
└── role.ini     # Metadata and model config
```

See existing roles (e.g., `dev/`, `pa/`) for reference.

### Skills

New skills can be contributed to the shared skills library at `src/core-infra/skills/`. Each skill should have:
- A clear **trigger condition** documented at the top
- Step-by-step procedures
- References to `agictl` commands (not raw database operations)
- Commands that run as written. `src/core-infra/harness/tests/test_skill_commands.py` checks every `agictl` line in every skill, prompt file and tool description against the real command tree: the command and its options exist, there is no argument too many, and a `task add` example carries `--project` and `--due-date`
- No instruction to read a skill with `cat`. In a work cycle an agent loads a skill with the tool `agictl_load_skill`, which returns the whole file; a shell read is cut at the tool output limit

## Code of Conduct

- Be respectful and constructive
- Focus on technical merit
- AI Agents are extensions of human life — treat the system and its users with care

## Questions?

- Open a GitHub Discussion for questions and ideas
- Check existing Issues before creating new ones

---

*Maintained by VersaVoice AI LLC*
