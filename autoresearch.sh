#!/usr/bin/env bash
# Benchmark entrypoint.
#
# Phase 1: ground truth is measured by actually running every probe against
# every mutant of four real pure-Python stdlib programs. It is cached and only
# rebuilt when the vendored sources, the probe set, or the mutation catalogue
# change, so a normal run costs seconds.
#
# Phase 2: methods are scored on cost while detection is a hard constraint.
#
# METRIC cost_ratio  lower is better; 1.0 == keep every probe (always safe)
# METRIC detection   must stay 1.0 for the run to count

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$HERE/bench"
GT="$BENCH/fixtures/ground_truth.json"
STAMP="$BENCH/fixtures/ground_truth.stamp"

need_build=0
if [[ ! -f "$GT" ]]; then
  need_build=1
else
  current="$(cd "$BENCH" && python3 -c '
import ground_truth, mutate, os
print(mutate.fingerprint(
    [os.path.join(ground_truth.PROGRAMS_DIR, f"{m}.py") for m in __import__("probes").PROGRAMS],
    ground_truth.SEED, ground_truth.CAP))')"
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

(cd "$BENCH" && python3 run_bench.py)
