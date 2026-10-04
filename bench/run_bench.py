"""Method runner: scores every method on every instance, emits METRIC lines.

The safety rule that makes this a real problem rather than a set-cover toy:
a method may only drop a probe if it has *investigated* every mutant it would
have needed that probe for. Uninvestigated mutants must be assumed detectable
by anything, so a method that explores nothing has to keep everything. The
question the benchmark asks is whether structure learned from a few real runs
generalises to the mutants a method never got to run.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import evaluate  # noqa: E402
import instance as inst_mod  # noqa: E402

BENCH = os.path.dirname(os.path.abspath(__file__))
GT = os.path.join(BENCH, "fixtures", "ground_truth.json")


def _safe_call(fn, inst, oracle):
    try:
        out = fn(inst, oracle)
    except BaseException as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"
    return list(out), None


def main() -> int:
    t_start = time.perf_counter()
    if not os.path.exists(GT):
        print(f"missing {GT}; run ground_truth.py first", file=sys.stderr)
        return 1

    instances = inst_mod.load(GT)
    specs = sys.argv[1:] or [
        "methods:keep_all",
        "methods:greedy_queried",
        "methods:random_queried",
        "methods:structure",
    ]

    results = {}
    for spec in specs:
        name = spec
        try:
            fn = evaluate.load_method(spec)
        except BaseException as exc:  # noqa: BLE001
            print(f"METHOD {name} FAILED TO LOAD: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        rows = []
        for inst in instances:
            oracle = inst.oracle(evaluate._default_budget(inst))
            chosen, err = _safe_call(fn, inst, oracle)
            chosen = [int(p) for p in chosen if 0 <= int(p) < inst.n_probes]
            det = inst.detect(chosen)
            cost = oracle.spent + inst.set_cost(chosen)
            rows.append(
                {
                    "module": inst.module,
                    "chosen": chosen,
                    "cost": cost,
                    "explore": oracle.spent,
                    "keep": inst.set_cost(chosen),
                    "queries": oracle.used,
                    "budget": evaluate._default_budget(inst),
                    "detect": det,
                    "full_cost": inst.full_cost(),
                    "ratio": cost / inst.full_cost() if inst.full_cost() else 0.0,
                    "error": err,
                }
            )
        results[name] = rows

    print("=" * 78)
    for name, rows in results.items():
        print(evaluate.report(name, rows))
        for r in rows:
            if r["error"]:
                print(f"      !! {r['module']}: {r['error']}")
    print("=" * 78)

    # Primary metric: mean cost ratio across all instances, for the method
    # under test. Detecting everything is a hard constraint, not a tradeoff.
    subject = "methods:structure"
    if subject in results:
        rows = results[subject]
        # Scoring: a method that misses a mutant is no better off than one that
        # kept everything, because you would have had to keep it to be safe.
        # So an infeasible instance is charged the safe fallback (ratio 1.0).
        feasible = [r for r in rows if r["detect"] >= 1.0]
        charged = [r["ratio"] if r["detect"] >= 1.0 else 1.0 for r in rows]
        mean_ratio = sum(charged) / len(charged)
        mean_detect = sum(r["detect"] for r in rows) / len(rows)
        print(f"subject_method      : {subject}")
        print(f"instances          : {len(rows)}")
        print(f"feasible_instances : {len(feasible)}")
        print(f"mean_detection     : {mean_detect:.6f}")
        print(f"mean_cost_ratio    : {mean_ratio:.6f}   (infeasible charged 1.0)")
        print(f"elapsed_seconds    : {time.perf_counter() - t_start:.1f}")
        print()
        print(f"METRIC cost_ratio={mean_ratio:.6f}")
        print(f"METRIC detection={mean_detect:.6f}")
        print(f"METRIC feasible_instances={len(feasible)}")
        print(f"METRIC elapsed_s={time.perf_counter() - t_start:.1f}")
        with open(os.path.join(BENCH, "fixtures", "last_run.json"), "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in results.items()}, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
