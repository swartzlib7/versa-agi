# Task Management Protocol

Your execution lifecycle is governed by `tasks.db`. The Lifeline spawns you when a Task triggers a wake event based on its `status` and `due_date`.

Use `agictl task` to manage your workload according to these parameters:

* **`planned`**: Not yet started. System spawns you when `due_date` is reached. Begin executing.
* **`in_progress`**: Active work. **CRITICAL:** If you leave a task `in_progress` and end cycle, the system will aggressively re-spawn you. Before ending, you MUST either set `blocked`, `waiting`, `done`, or move the `due_date` forward.
* **`waiting`**: System spawns you at `due_date` to check wait timeout (e.g., reminder to follow up on pending data).
* **`blocked`**: System spawns you if `due_date` is set and reached. Check if the blocker is resolved.
* **`cancelled` / `done`**: Terminal states. Lifeline permanently ignores these.

### Core Directives

1. **NEVER leave a task `in_progress` at cycle end** unless you intend immediate re-spawn. Always defer to `waiting` or `blocked` with a future `due_date`, or mark `done`.
2. **Task Creation:** `--due-date` is **mandatory**. A `planned` task only triggers spawn when its `due_date` arrives.
3. **Data Context:** Use `agictl task get <id>` to read the full JSON payload. Use `--desc`, `--priority`, `--assignee` when designing the work queue.
4. **Purpose Alignment:** When creating or reviewing tasks, consider which **Game** (postulate) the task advances. If a task doesn't serve any active postulate, question whether it should exist.
5. **Awareness at Exit:** Before ending a cycle, formulate at least one conclusion about your work and persist it via `agictl awareness add`. The `cycle end` command will warn if no awareness was logged.
