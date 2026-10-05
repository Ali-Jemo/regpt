"""Validate the CI workflow: parses, wires outputs correctly, and asserts on
metrics that would actually fail the build.

Checked before pushing, so a malformed workflow is caught locally rather than by
a red CI run that costs a cycle and hides the real problem behind noise.
"""

from __future__ import annotations

import sys
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github/workflows/benchmark.yml"

failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    if not cond:
        failures.append(label + (f" -- {detail}" if detail else ""))


def main() -> int:
    text = WF.read_text(encoding="utf-8")

    try:
        import yaml
    except ImportError:
        print("pyyaml unavailable; checking structure textually")
        check("steps.bench.outputs.metrics" in text,
              "workflow does not read the metrics step output")
        check("echo \"${METRICS}\"" in text,
              "report step does not use the env var")
        return _report(failures)

    doc = yaml.safe_load(text)
    check(isinstance(doc, dict), "workflow is not a mapping")
    check("jobs" in doc, "workflow has no jobs")

    steps = doc["jobs"]["benchmark"]["steps"]
    names = [s.get("name") or s.get("uses", "?") for s in steps]
    print(f"steps: {names}")

    bench = next((s for s in steps if s.get("id") == "bench"), None)
    check(bench is not None, "no step declares id: bench")

    assertion = next((s for s in steps if s.get("name") == "Assert the reported contract"), None)
    check(assertion is not None, "no assertion step")
    if assertion:
        env = assertion.get("env", {})
        check("steps.bench.outputs.metrics" in str(env),
              "assertion step does not receive the metrics output",
              f"env={env}")
        # A bare `${{ }}` inside `run:` would splice the metrics into the script
        # text, so the shell would try to execute each METRIC line as a command.
        # That is exactly the bug this check exists to prevent.
        check("steps.bench.outputs.metrics" not in assertion["run"],
              "assertion step splices the output into the script instead of env")

    report = next((s for s in steps if s.get("name") == "Report"), None)
    check(report is not None, "no report step")
    if report:
        check('echo "${METRICS}"' in report["run"],
              "report step does not echo the env var")
        check("steps.bench.outputs.metrics" not in report["run"],
              "report step splices the output into the script instead of env")

    # The assertion must actually reject a regression, not merely be present.
    script = assertion["run"] if assertion else ""
    check("exit 1" in script, "assertion step never fails the build")
    check("detection" in script, "assertion does not check detection")
    check("1.0" in script, "assertion does not check the keep_all reference")

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print(f"\n{len(failures)} workflow check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("workflow checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
