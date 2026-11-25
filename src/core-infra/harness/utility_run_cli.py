"""Subprocess bridge: execute a Utility Model run under the **harness venv**.

WHY THIS EXISTS — the two-venv split
------------------------------------
Versa AGi deploys two deliberately separate Python virtual environments:

  * **agictl / agitop venv** — ``/opt/versa-agi/venv``
    Lightweight by design: ``click rich textual psutil Pillow``. Keeps the CLI
    and Mission-Control dashboard small and fast to launch. This is the venv the
    ``agictl`` wrapper runs under.

  * **harness venv** — ``/usr/local/lib/versa-agi/venv``
    Carries the heavyweight model SDKs (``openai``, the full ``langchain*``
    stack). This is the venv the agent harness runs under.

Utility Model generation is *harness* code (``harness/utility_runner.py`` +
``harness/generation.py``) and needs those model SDKs. But it is *dispatched* by
``agictl utility run`` / ``run-due-tasks``, which execute under the lightweight
agictl venv — where ``import openai`` fails with ``ModuleNotFoundError``.

Rather than duplicate the entire SDK stack into the agictl venv (hundreds of MB,
two copies to keep in sync), agictl hands the actual run to *this* module under
the harness interpreter. The interpreter and the code tree are decoupled: this
bridge runs under the harness venv's ``python`` while importing the very same
``CORE_INFRA`` code tree agictl already uses (passed via ``PYTHONPATH``). So
there is exactly one copy of the runner logic and exactly one place the model
SDKs live.

The DB orchestration (task status, ``task_progress`` notes, alerts, spawn wakes)
stays in agictl — only the model execution is delegated here.

Protocol
--------
``stdin``/``argv[1]`` carries a JSON object of ``run_utility_model`` parameters
(``um_id`` plus keyword args). The single machine-readable result line is
emitted on stdout prefixed with ``RESULT_MARKER`` so the caller can isolate it
from any incidental SDK stdout noise:

    @@UTIL_RUN_RESULT@@{"success": true, "result": {...}}
    @@UTIL_RUN_RESULT@@{"success": false, "code": "...", "error": "..."}

Exit code is ``0`` on success, ``1`` on a Utility run error, ``2`` on bad input.
"""

from __future__ import annotations

import json
import sys

RESULT_MARKER = "@@UTIL_RUN_RESULT@@"


def _emit(payload: dict) -> None:
    sys.stdout.write(RESULT_MARKER + json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        raw = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
        params = json.loads(raw)
        um_id = params.pop("um_id")
    except (ValueError, IndexError, KeyError) as e:
        _emit({"success": False, "code": "bad_params", "error": f"invalid run params: {e}"})
        return 2

    # Imported here (not at module top) so a parameter error reports cleanly even
    # if the heavyweight SDK import chain is slow or noisy.
    from harness.utility_runner import UtilityRunError, run_utility_model

    try:
        result = run_utility_model(um_id, **params)
        _emit({"success": True, "result": result})
        return 0
    except UtilityRunError as e:
        _emit({"success": False, "code": e.code, "error": e.message})
        return 1
    except Exception as e:  # noqa: BLE001 — surface any failure as a structured result
        _emit({"success": False, "code": "utility_error", "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
