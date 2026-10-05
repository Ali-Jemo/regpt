#!/usr/bin/env bash
# Benchmark entrypoint.
#
# Stage 1: soundness and behaviour tests. These gate the run. A benchmark that
#   quietly measures the wrong thing is worse than no benchmark, because the
#   number still looks plausible, so the tests check the harness itself: that the
#   subject method cannot read the answer, that the oracle bills every query,
#   that the cache cannot go stale, and that every probe actually works.
#
# Stage 2: ground truth, measured by running every probe against every mutant of
#   six real pure-Python stdlib programs. Cached, and rebuilt only when the
#   vendored sources, the probe definitions, the execution oracle, or the
#   mutation settings change.
#
# Stage 3: methods are scored on cost, with detection as a hard constraint.
#
# METRIC cost_ratio  lower is better; 1.0 == keep every probe (always safe)
# METRIC detection   must stay 1.0 for the run to count
#
# Set REGPT_SKIP_TESTS=1 to skip stage 1 while iterating on the method. The
# tests are cheap relative to a rebuild but not free, and they only need
# re-running when the harness changes.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$HERE/bench"
TESTS="$HERE/tests"
GT="$BENCH/fixtures/ground_truth.json"

if [[ "${REGPT_SKIP_TESTS:-0}" != "1" ]]; then
  echo "== workflow ==" >&2
  python3 "$TESTS/test_workflow.py" >&2
  echo "== soundness ==" >&2
  python3 "$TESTS/test_soundness.py" >&2
  echo "== behaviour ==" >&2
  python3 "$TESTS/test_behaviour.py" >&2
fi

need_build=0
if [[ ! -f "$GT" ]]; then
  need_build=1
else
  current="$(cd "$BENCH" && python3 -c '
import ground_truth, mutate, os, probes
print(mutate.fingerprint(
    [os.path.join(ground_truth.PROGRAMS_DIR, f"{m}.py") for m in probes.PROGRAMS],
    ground_truth.SEED, ground_truth.CAP,
    extra_paths=[os.path.join(ground_truth.BENCH, "probes.py"),
                 os.path.join(ground_truth.BENCH, "execute.py")]))')"
  cached="$(python3 -c '
import json,sys
try:
    print(json.load(open(sys.argv[1]))["fingerprint"])
except Exception:
    print("")' "$GT")"
  if [[ "$current" != "$cached" ]]; then
    need_build=1
  fi
fi

if [[ "$need_build" == "1" ]]; then
  echo "ground truth stale or missing; rebuilding by real execution ..." >&2
  (cd "$BENCH" && python3 ground_truth.py "$GT")
fi

# Run the benchmark once. The full report is printed; the metrics are also
# written to the Actions step output so CI can assert on them without
# re-parsing logs. Locally that output file is unset and nothing is duplicated.
METRICS_FILE="$(mktemp)"
trap 'rm -f "$METRICS_FILE"' EXIT
(cd "$BENCH" && python3 run_bench.py) | tee "$METRICS_FILE"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "metrics<<METRIC_EOF"
    grep '^METRIC ' "$METRICS_FILE" || true
    echo "METRIC_EOF"
  } >> "$GITHUB_OUTPUT"
fi
