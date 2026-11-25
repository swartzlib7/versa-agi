#!/usr/bin/env python3
"""Run the unit tests on every interpreter Versa AGi runs on, and judge them together.

    python3 harness/tests/run_tests.py            # all interpreters, in parallel
    python3 harness/tests/run_tests.py --only agictl harness
    python3 harness/tests/run_tests.py --json /tmp/matrix.json

Why one command. The product runs on three Python interpreters, and each has
packages another lacks:

    harness   /usr/local/lib/versa-agi/venv   LangChain, the provider SDKs
    agictl    /opt/versa-agi/venv             click, rich, textual
    system    /usr/bin/python3                what Lifeline's scripts use

A test that needs a package its interpreter does not have skips there with a
reason. Skips alone could hide a test that runs nowhere, so this command reads
the three runs together. The result is clean only when:

    1. no test fails or errors on any interpreter, and
    2. every test passes on at least one interpreter.

The only tests allowed to run nowhere are the paid live probes
(test_model_driver_live), which need VERSA_DRIVER_LIVE_TEST=1 and real keys.

Exit code: 0 clean, 1 not clean, 2 an interpreter is missing (see --allow-missing).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INTERPRETERS = (
    ("harness", "/usr/local/lib/versa-agi/venv/bin/python"),
    ("agictl", "/opt/versa-agi/venv/bin/python3"),
    ("system", "/usr/bin/python3"),
)

# Tests that are not expected to pass anywhere without being asked for.
NEVER_RUN_ALLOWED = ("harness.tests.test_model_driver_live.",)

MODULE_SKIP = "unittest.loader.ModuleSkipped."

# Run inside each interpreter: discover, run, and write {test id: [status, detail]}.
_CHILD = r'''
import json, sys, unittest

class Recorder(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.status = {}
    def _set(self, test, status, detail=""):
        current = self.status.get(test.id(), ["ok", ""])[0]
        if status in ("fail", "error") or current not in ("fail", "error"):
            self.status[test.id()] = [status, detail[-1500:]]
    def addSuccess(self, test):
        self._set(test, "ok")
    def addFailure(self, test, err):
        self._set(test, "fail", self._exc_info_to_string(err, test))
    def addError(self, test, err):
        self._set(test, "error", self._exc_info_to_string(err, test))
    def addSkip(self, test, reason):
        self._set(test, "skip", reason)
    def addExpectedFailure(self, test, err):
        self._set(test, "ok")
    def addUnexpectedSuccess(self, test):
        self._set(test, "fail", "unexpected success")
    def addSubTest(self, test, subtest, err):
        if err is not None:
            kind = "fail" if issubclass(err[0], test.failureException) else "error"
            self._set(test, kind, self._exc_info_to_string(err, test))
        else:
            self._set(test, "ok")

suite = unittest.defaultTestLoader.discover("harness/tests", top_level_dir=".")
recorder = Recorder()
suite.run(recorder)
with open(sys.argv[1], "w", encoding="utf-8") as out:
    json.dump(recorder.status, out)
'''


def judge(results: dict[str, dict[str, list]]) -> list[str]:
    """What stops the result from being clean. ``results`` is
    {interpreter: {test id: [status, detail]}}; an empty list means clean."""
    problems: list[str] = []
    for name, statuses in results.items():
        for test, (status, detail) in sorted(statuses.items()):
            if status in ("fail", "error"):
                last = detail.strip().splitlines()[-1] if detail.strip() else ""
                problems.append(f"{status.upper()} on {name}: {test}" + (f" — {last[:160]}" if last else ""))

    def module_of(test: str) -> str:
        return ".".join(test.split(".")[:3])   # harness.tests.test_x

    # A module skipped as a whole shows as one entry; its tests must run somewhere else.
    skipped_modules: dict[str, set[str]] = {}
    ran_modules: dict[str, set[str]] = {}
    for name, statuses in results.items():
        for test in statuses:
            if test.startswith(MODULE_SKIP):
                skipped_modules.setdefault(test[len(MODULE_SKIP):], set()).add(name)
            elif test.startswith("harness.tests."):
                ran_modules.setdefault(module_of(test), set()).add(name)
    for module in sorted(skipped_modules):
        if module not in ran_modules and not (module + ".").startswith(NEVER_RUN_ALLOWED):
            problems.append(f"RUNS NOWHERE: module {module} is skipped on every interpreter")

    passed = {test for statuses in results.values() for test, (status, _) in statuses.items() if status == "ok"}
    seen = {test for statuses in results.values() for test in statuses
            if test.startswith("harness.tests.")}
    for test in sorted(seen - passed):
        if test.startswith(NEVER_RUN_ALLOWED):
            continue
        if any(statuses.get(test, ["", ""])[0] in ("fail", "error") for statuses in results.values()):
            continue   # already reported above
        reasons = sorted({statuses[test][1] for statuses in results.values() if test in statuses})
        problems.append(f"PASSES NOWHERE: {test} — skipped: {'; '.join(reasons)[:200]}")
    return problems


def summary(statuses: dict[str, list]) -> str:
    count = {"ok": 0, "skip": 0, "fail": 0, "error": 0}
    for status, _ in statuses.values():
        count[status] = count.get(status, 0) + 1
    return (f"{len(statuses):5d} results: {count['ok']} passed, {count['skip']} skipped, "
            f"{count['fail']} failed, {count['error']} errors")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--only", nargs="+", choices=[name for name, _ in INTERPRETERS],
                        help="run these interpreters only (the 'passes somewhere' check then covers them alone)")
    parser.add_argument("--allow-missing", action="store_true",
                        help="an interpreter that is not installed is left out instead of stopping the run")
    parser.add_argument("--json", metavar="FILE", help="write the per-test results of every interpreter here")
    args = parser.parse_args(argv)

    chosen = [(name, path) for name, path in INTERPRETERS if not args.only or name in args.only]
    missing = [(name, path) for name, path in chosen if not os.path.exists(path)]
    for name, path in missing:
        print(f"{name:8s} not found: {path}")
    if missing and not args.allow_missing:
        print("Stopped: an interpreter is missing. --allow-missing runs the others.")
        return 2
    chosen = [pair for pair in chosen if pair not in missing]
    if not chosen:
        print("Nothing to run.")
        return 2

    started = time.time()
    running = []
    with tempfile.TemporaryDirectory() as folder:
        for name, path in chosen:
            out = os.path.join(folder, f"{name}.json")
            log = open(os.path.join(folder, f"{name}.log"), "w", encoding="utf-8")
            process = subprocess.Popen([path, "-c", _CHILD, out], cwd=CORE_INFRA,
                                       stdout=log, stderr=subprocess.STDOUT)
            running.append((name, process, out, log))
        results: dict[str, dict[str, list]] = {}
        broken = []
        for name, process, out, log in running:
            process.wait()
            log.close()
            try:
                with open(out, encoding="utf-8") as handle:
                    results[name] = json.load(handle)
            except (OSError, ValueError):
                with open(log.name, encoding="utf-8", errors="replace") as handle:
                    tail = handle.read()[-600:]
                broken.append(f"NO RESULT from {name}: the run ended without one. Last output: {tail.strip()}")

    for name, _ in chosen:
        if name in results:
            print(f"{name:8s} {summary(results[name])}")
    problems = broken + judge(results)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1, sort_keys=True)
    print(f"({time.time() - started:.0f} s)")
    if problems:
        print(f"\nNOT CLEAN — {len(problems)} problem(s):")
        for line in problems:
            print("  " + line)
        return 1
    everywhere = {test for statuses in results.values() for test in statuses
                  if test.startswith("harness.tests.") and not test.startswith(NEVER_RUN_ALLOWED)}
    print(f"\nCLEAN — no failures, and each of the {len(everywhere)} tests passed on at least one interpreter"
          + (" (paid live probes left out)." if not args.only else " of those chosen."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
