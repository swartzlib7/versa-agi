#!/bin/bash
# ORG-D5 — FK orphan sweep (run: sudo bash /tmp/versa_agi_d5_fk_sweep.sh)
set -euo pipefail
TASKS=/var/lib/versa-agi/coa/tasks.db
echo "BEFORE: $(sqlite3 "$TASKS" 'PRAGMA foreign_key_check;' | wc -l) orphans"
sqlite3 "$TASKS" <<'SQL'
BEGIN;
DELETE FROM task_progress
 WHERE task_id NOT IN (SELECT id FROM tasks);
UPDATE agent_awareness
   SET action_conclusion_id = NULL,
       updated_at = datetime('now')
 WHERE action_conclusion_id IS NOT NULL
   AND action_conclusion_id NOT IN (SELECT id FROM agent_awareness);
COMMIT;
SQL
echo "AFTER: $(sqlite3 "$TASKS" 'PRAGMA foreign_key_check;' | wc -l) orphans"
sqlite3 "$TASKS" "PRAGMA foreign_key_check;"
echo D5_DONE
