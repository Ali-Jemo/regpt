"""Ground truth: the full observation matrix, measured by actual execution.

For every program: run every probe against the original source (baseline
observation vector) and against every mutant (mutant observation vector). The
influence matrix entry [m][p] is 1 when mutant m changes the observation of
probe p relative to the original. This is exhaustive by construction -- no
heuristics, no labels, purely observed behaviour.
"""

from __future__ import annotations

import json
import os
import sys
import time

import execute
import mutate
import probes
import structure

BENCH = os.path.dirname(os.path.abspath(__file__))
PROGRAMS_DIR = os.path.join(BENCH, "programs")
SEED = 20261004
CAP = 140
PROBE_TIMEOUT = 3.0


def build(cache_path: str, workers: int = 4, verbose: bool = True) -> dict:
    gt = {
        "seed": SEED,
        "cap": CAP,
        "fingerprint": mutate.fingerprint(
            [os.path.join(PROGRAMS_DIR, f"{m}.py") for m in probes.PROGRAMS],
            SEED,
            CAP,
            extra_paths=[os.path.join(BENCH, "probes.py")],
        ),
        "programs": {},
    }
    for module in probes.PROGRAMS:
        path = os.path.join(PROGRAMS_DIR, f"{module}.py")
        if not os.path.exists(path):
            raise SystemExit(f"missing program source: {path}")
        original = open(path, encoding="utf-8").read()

        probe_list = getattr(probes, module.upper())
        probe_sources = [probes.probe_source(module, body) for _, _, body in probe_list]
        costs = [c for _, c, _ in probe_list]
        ids = [i for i, _, _ in probe_list]

        # Static structure: which program functions each probe reaches. Free to
        # compute, and the only signal a method has about probes it never ran.
        ftable = structure.function_table(original)
        signatures = [structure.probe_signature(ps, ftable) for ps in probe_sources]

        # 1. baseline observations
        t0 = time.perf_counter()
        base = execute.fanout(
            [(original, ps, PROBE_TIMEOUT) for ps in probe_sources], workers
        )
        base_s = time.perf_counter() - t0

        # 2. mutants
        mutants, sources = mutate.build(path, SEED, CAP)
        jobs = []
        for mid in sorted(sources):
            for ps in probe_sources:
                jobs.append((sources[mid], ps, PROBE_TIMEOUT))

        t0 = time.perf_counter()
        results = execute.fanout(jobs, workers)
        mut_s = time.perf_counter() - t0

        order = sorted(sources)
        n = len(order)
        influence = []
        for i in range(n):
            row = []
            for p in range(len(probe_sources)):
                got = results[i * len(probe_sources) + p]
                ref = base[p]
                # A probe that already errors on the original cannot discriminate.
                if ref["status"] in ("harness-error", "timeout", "crash"):
                    row.append(0)
                else:
                    row.append(0 if got["hash"] == ref["hash"] else 1)
            influence.append(row)

        undetectable = [
            order[i] for i in range(n) if not any(influence[i])
        ]
        dead_probes = [
            ids[p] for p in range(len(probe_sources)) if not any(influence[m][p] for m in range(n))
        ]
        failing_baseline = [
            ids[p] for p in range(len(probe_sources)) if base[p]["status"] != "ok"
        ]

        gt["programs"][module] = {
            "source": original,
            "probe_ids": ids,
            "probe_costs": costs,
            "probe_sources": probe_sources,
            "probe_signatures": signatures,
            "baseline": base,
            "mutants": order,
            "mutant_sources": sources,
            "influence": influence,
            "n_mutants": n,
            "n_probes": len(probe_sources),
            "undetectable": undetectable,
            "dead_probes": dead_probes,
            "failing_baseline": failing_baseline,
            "build_seconds": round(base_s + mut_s, 2),
        }
        if verbose:
            print(
                f"  {module:10s} {n:3d} mutants x {len(probe_sources):2d} probes "
                f"= {n * len(probe_sources):5d} execs in {base_s + mut_s:6.1f}s  "
                f"live_mutants={n - len(undetectable):3d}  dead_probes={len(dead_probes)}  "
                f"baseline_failures={len(failing_baseline)}",
                flush=True,
            )

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(gt, fh)
    os.replace(tmp, cache_path)
    return gt


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BENCH, "fixtures", "ground_truth.json")
    print("building ground truth ...", flush=True)
    data = build(out)
    print("wrote", out)
