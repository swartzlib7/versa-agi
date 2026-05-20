# Contributing to Versa AGi

Thank you for your interest in Versa AGi! This document outlines the contribution process.

## License

Versa AGi is licensed under the [Business Source License 1.1](../LICENSE.md). By contributing, you agree that your contributions will be governed by this license.

## Getting Started

1. **Read the [README](../README.md)** for project overview and installation
2. **Read the [System Design](../design/Versa AGi - System Design.md)** for architectural understanding
3. **Fork the repository** and create a feature branch

## Development Setup

### Prerequisites

- Linux (Ubuntu 24.04 recommended) or macOS
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
   - Poise/Skills: markdown format, see existing files in `src/core-infra/config/` and `src/coa-env/.agent/skills/`
4. **Test** by running `sudo ./src/setup.sh --update` and verifying with `sudo agitop`
5. **Submit a Pull Request** with a clear description of the change

### Agent Roles

Community members can contribute new agent roles by adding a directory to `src/core-infra/config/roles/`:

```
config/roles/your-role/
├── poise.md     # Behavioral framework
└── role.ini     # Metadata and model config
```

See existing roles (e.g., `dev/`, `pa/`) for reference.

### Skills

New skills can be contributed to the shared skills library at `src/coa-env/.agent/skills/`. Each skill should have:
- A clear **trigger condition** documented at the top
- Step-by-step procedures
- References to `agictl` commands (not raw database operations)

## Code of Conduct

- Be respectful and constructive
- Focus on technical merit
- AI Agents are extensions of human life — treat the system and its users with care

## Questions?

- Open a GitHub Discussion for questions and ideas
- Check existing Issues before creating new ones

---

*Maintained by VersaVoice AI LLC*
