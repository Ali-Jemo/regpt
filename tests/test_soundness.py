"""Soundness tests: the properties that make the benchmark mean something.

These are the checks that would have caught the bugs actually hit while building
this: a staleness fingerprint that ignored the probe definitions, a call-graph
resolver that silently reported three probes as reaching nothing, a cost model
that made the safe baseline unbeatable by construction, and a set of probes that
had been raising since they were written.

A benchmark that quietly measures the wrong thing is worse than no benchmark,
because the number still looks plausible. Every test here is about the number
being trustworthy, not about the method scoring well.

Run: python3 tests/test_soundness.py
"""

from __future__ import annotations

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
sys.path.insert(0, BENCH)

import callgraph  # noqa: E402
import evaluate  # noqa: E402
import execute  # noqa: E402
import instance as inst_mod  # noqa: E402
import methods  # noqa: E402
import mutate  # noqa: E402
import probes  # noqa: E402

GT_PATH = os.path.join(BENCH, "fixtures", "ground_truth.json")

_failures: list[str] = []
_checks = 0


def check(condition: bool, label: str, detail: str = "") -> None:
    global _checks
    _checks += 1
    if not condition:
        _failures.append(f"{label}" + (f" -- {detail}" if detail else ""))


def _gt():
    with open(GT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# 1. The subject method must not read the answer.
# --------------------------------------------------------------------------

def test_subject_method_never_reads_ground_truth() -> None:
    """A method that peeks at the influence matrix scores perfectly and proves
    nothing. This is the one test that keeps the benchmark honest, so it is
    written as a static check over the method's own source rather than a
    behavioural one: a runtime check could be satisfied by a method that only
    sometimes reaches for the answer."""
    src = open(os.path.join(BENCH, "methods.py"), encoding="utf-8").read()
    start = src.index("def structure(")
    end = src.index("def _settled")
    body = src[start:end]

    forbidden = (
        "instance.row",
        "instance.detect",
        "instance.live",
        "instance.influence",
        "instance.mutants",
        "instance.signatures",
        "instance.reaches",
        "instance.fn_of_line",
        "_greedy_cover(",
    )
    for name in forbidden:
        check(name not in body,
              "subject method reads ground truth",
              f"structure() references {name!r}")

    # And it must actually go through the oracle, or it never learns anything.
    check("_explore_spread" in body or "oracle.query" in body,
          "subject method never consults the oracle",
          "structure() must spend budget to learn anything")


def test_baselines_return_valid_probe_sets() -> None:
    """Baselines are allowed to be poor -- that is the point of them -- but every
    one must return a well-formed set of in-range indices, and must respect the
    oracle's budget. A baseline that crashes is a broken harness, not a weak
    baseline."""
    for inst in inst_mod.load(GT_PATH):
        for name in ("keep_all", "greedy_queried", "random_queried", "structure"):
            fn = getattr(methods, name)
            oracle = inst.oracle(evaluate._default_budget(inst))
            chosen = fn(inst, oracle)
            check(isinstance(chosen, list),
                  f"{name} did not return a list", inst.module)
            check(all(isinstance(p, int) for p in chosen),
                  f"{name} returned a non-integer probe index", inst.module)
            check(all(0 <= p < inst.n_probes for p in chosen),
                  f"{name} returned an out-of-range probe index", inst.module)
            check(len(set(chosen)) == len(chosen),
                  f"{name} returned duplicate probe indices", inst.module)
            check(oracle.spent <= oracle.budget,
                  f"{name} overspent the exploration budget", inst.module)


# --------------------------------------------------------------------------
# 2. The oracle must cost what it says it costs.
# --------------------------------------------------------------------------

def test_oracle_bills_every_query() -> None:
    for inst in inst_mod.load(GT_PATH):
        o = inst.oracle(200)
        before_spent, before_left = o.spent, o.remaining()
        row = o.query(0)
        check(row is not None, "oracle refused an affordable query",
              f"{inst.module}: budget 200, cost {o.query_cost}")
        check(o.spent == before_spent + o.query_cost,
              "oracle did not bill a query",
              f"{inst.module}: spent {before_spent} -> {o.spent}, cost {o.query_cost}")
        check(o.remaining() == before_left - 1,
              "oracle did not decrement remaining",
              f"{inst.module}: {before_left} -> {o.remaining()}")


def test_oracle_refuses_to_overspend() -> None:
    for inst in inst_mod.load(GT_PATH):
        budget = o_budget = inst.oracle(10 ** 6).budget
        o = inst.oracle(o_budget)
        while o.remaining() > 0:
            o.query(0)
        check(o.spent <= o.budget, "oracle overspent its budget",
              f"{inst.module}: spent {o.spent} of {o.budget}")
        check(o.query(0) is None, "oracle answered after exhausting its budget",
              f"{inst.module}: remaining {o.remaining()}")


def test_query_cost_is_a_real_fraction_of_the_suite() -> None:
    """The phase-1 bug priced a query as a *full* pass with a *full-pass* budget,
    which made exploration alone cost 0.68-0.82 of total and the safe baseline
    unbeatable by construction. Guard the ratio so that cannot silently return."""
    for inst in inst_mod.load(GT_PATH):
        o = inst.oracle(10 ** 6)
        frac = o.query_cost / inst.full_cost()
        check(0.01 <= frac <= 0.5,
              "query cost is an unrealistic fraction of the suite",
              f"{inst.module}: {frac:.2f} of full cost")


# --------------------------------------------------------------------------
# 3. The cache must not go stale.
# --------------------------------------------------------------------------

def test_fingerprint_covers_probes_and_programs() -> None:
    """The bug: the fingerprint hashed only the program sources, so editing
    `probes.py` -- which changes the influence matrix by construction -- left a
    stale matrix in place and every later run scored against it."""
    paths = [os.path.join(BENCH, "programs", f"{m}.py") for m in probes.PROGRAMS]
    probe_path = os.path.join(BENCH, "probes.py")

    base = mutate.fingerprint(paths, 7, 40)
    with_probes = mutate.fingerprint(paths, 7, 40, extra_paths=[probe_path])
    check(base != with_probes,
          "fingerprint ignores the probe definitions",
          "editing probes.py would serve a stale influence matrix")

    exec_path = os.path.join(BENCH, "execute.py")
    with_exec = mutate.fingerprint(paths, 7, 40, extra_paths=[exec_path])
    check(base != with_exec,
          "fingerprint ignores the execution oracle",
          "editing how an observation is rendered would serve a stale matrix")

    other = mutate.fingerprint(paths[:-1], 7, 40)
    check(base != other, "fingerprint ignores the program set")

    check(mutate.fingerprint(paths, 7, 40) != mutate.fingerprint(paths, 8, 40),
          "fingerprint ignores the seed")
    check(mutate.fingerprint(paths, 7, 40) != mutate.fingerprint(paths, 7, 41),
          "fingerprint ignores the mutant cap")


def test_cached_fingerprint_matches_the_current_sources() -> None:
    """The cache in the repo must be the one these sources produce. If it does
    not, every number being reported is from a different benchmark."""
    gt = _gt()
    paths = [os.path.join(BENCH, "programs", f"{m}.py") for m in probes.PROGRAMS]
    current = mutate.fingerprint(
        paths, gt["seed"], gt["cap"],
        extra_paths=[os.path.join(BENCH, "probes.py"),
                     os.path.join(BENCH, "execute.py")],
    )
    check(current == gt["fingerprint"],
          "cached ground truth does not match the current sources",
          f"cache {gt['fingerprint']} vs sources {current}")


# --------------------------------------------------------------------------
# 4. Probes must actually work.
# --------------------------------------------------------------------------

def test_every_probe_returns_a_value_on_the_original() -> None:
    """The bug: five probes had been raising since they were written --
    `ndif` is not a function, and `shlex.__init__` takes no `commenters=`.
    They contributed nothing to the matrix and nothing was wrong with the
    result, which is exactly what makes this worth a test."""
    gt = _gt()
    broken = []
    for module, prog in gt["programs"].items():
        for pid, obs in zip(prog["probe_ids"], prog["baseline"]):
            if obs["status"] != "ok":
                broken.append(f"{module}/{pid}: {obs['text'][:60]}")
    check(not broken,
          "probes that never return a value",
          f"{len(broken)} inert: " + "; ".join(broken[:6]))


def test_probe_reach_is_never_spuriously_empty() -> None:
    """The bug: `probe_reach` followed only the probe's own calls, so a probe
    that reached the program through a helper defined beside it reported an
    empty reach -- and the predictor then claimed nothing could ever see it."""
    gt = _gt()
    for module, prog in gt["programs"].items():
        funcs = callgraph.qualified_functions(prog["source"])
        empty = [
            pid for pid, src in zip(prog["probe_ids"], prog["probe_sources"])
            if not callgraph.probe_reach(src, funcs)
        ]
        check(not empty,
              "probe reach is empty but the probe still returns a value",
              f"{module}: {empty}")


def test_probe_signature_matches_probe_reach() -> None:
    """A probe that returns a value must reach *something*. If the signature is
    empty while the probe works, the static signal is missing something."""
    gt = _gt()
    for module, prog in gt["programs"].items():
        for pid, sig in zip(prog["probe_ids"], prog.get("probe_signatures", [])):
            check(isinstance(sig, list),
                  "probe signature is not a list", f"{module}/{pid}")


# --------------------------------------------------------------------------
# 5. Mutants must be real programs.
# --------------------------------------------------------------------------

def test_mutants_are_valid_and_distinct() -> None:
    gt = _gt()
    total = 0
    for module, prog in gt["programs"].items():
        original = prog["source"]
        for mid, src in prog["mutant_sources"].items():
            total += 1
            try:
                ast.parse(src)
            except SyntaxError as exc:
                check(False, "mutant is not a valid program", f"{module}/{mid}: {exc}")
                continue
            check(src != original,
                  "mutant is identical to the original", f"{module}/{mid}")
        check(len(prog["mutants"]) == len(prog["influence"]),
              "mutant list and influence matrix disagree in length", module)
    check(total > 0, "no mutants were built at all")


def test_influence_matrix_shape_and_range() -> None:
    gt = _gt()
    for module, prog in gt["programs"].items():
        rows, cols = len(prog["influence"]), prog["n_probes"]
        for i, row in enumerate(prog["influence"]):
            check(len(row) == cols,
                  "influence row has the wrong width",
                  f"{module} row {i}: {len(row)} != {cols}")
            for v in row:
                check(v in (0, 1),
                      "influence entry is not a boolean", f"{module} row {i}")


def test_undetectable_mutants_have_empty_influence() -> None:
    """A mutant is listed undetectable exactly when no probe sees it. If those
    two ever disagree, the scoring charges a method for missing something that
    was never observable."""
    gt = _gt()
    for module, prog in gt["programs"].items():
        for mid in prog["undetectable"]:
            i = prog["mutants"].index(mid)
            check(not any(prog["influence"][i]),
                  "mutant marked undetectable but some probe sees it",
                  f"{module}/{mid}")


# --------------------------------------------------------------------------
# 6. Scoring arithmetic.
# --------------------------------------------------------------------------

def test_keep_all_costs_exactly_the_full_suite() -> None:
    for inst in inst_mod.load(GT_PATH):
        o = inst.oracle(evaluate._default_budget(inst))
        chosen = methods.keep_all(inst, o)
        check(inst.set_cost(chosen) == inst.full_cost(),
              "keep_all does not cost the full suite", inst.module)
        check(o.spent == 0, "keep_all spent budget", inst.module)
        check(inst.detect(chosen) == 1.0,
              "keep_all does not detect everything", inst.module)


def test_detect_counts_live_mutants_only() -> None:
    for inst in inst_mod.load(GT_PATH):
        check(inst.detect([]) == (1.0 if not inst.live() else 0.0),
              "empty probe set reports nonzero detection", inst.module)
        check(inst.detect(list(range(inst.n_probes))) == 1.0,
              "full probe set does not detect everything", inst.module)


def test_dead_mutants_are_not_scored() -> None:
    """Detection is over *live* mutants. If it counted dead ones, no method
    could ever score 1.0 and the metric would be silently unwinnable.

    An instance where *every* mutant is live is legitimate -- a well-probed
    large module can expose every mutation it generates -- so the invariant
    here is only that an empty probe set scores zero, not that some mutants
    must be dead.
    """
    for inst in inst_mod.load(GT_PATH):
        check(inst.detect([]) == (1.0 if not inst.live() else 0.0),
              "dead mutants leak into the detection score", inst.module)
        check(all(not any(inst.row(m)) for m in range(inst.n_mutants)
                  if m not in inst.live()),
              "an 'undetectable' mutant is in fact seen by some probe",
              inst.module)


# --------------------------------------------------------------------------
# 7. The execution oracle itself.
# --------------------------------------------------------------------------

def test_execution_is_deterministic() -> None:
    gt = _gt()
    for module in ("difflib", "fnmatch"):
        prog = gt["programs"][module]
        src = prog["source"]
        ps = prog["probe_sources"][:4]
        a = execute.fanout([(src, s, 3.0) for s in ps], 4)
        b = execute.fanout([(src, s, 3.0) for s in ps], 4)
        check([x["hash"] for x in a] == [x["hash"] for x in b],
              "probe execution is not deterministic", module)


def test_execution_is_isolated_from_the_child() -> None:
    """A probe that raises must be reported, not take the harness with it. A
    probe that loops forever must time out rather than hang the run."""
    ps = "def __regpt_probe__(_m):\n    raise ValueError('boom')\n"
    out = execute.execute("x = 1\n", ps, timeout=2.0)
    check(out["status"] == "exc", "a raising probe was not reported as an exception",
          f"status={out['status']}")
    check("boom" in out["text"], "the exception detail was lost", out["text"][:40])

    ps_loop = "def __regpt_probe__(_m):\n    while True:\n        pass\n"
    out = execute.execute("x = 1\n", ps_loop, timeout=1.0)
    check(out["status"] == "timeout", "a looping probe did not time out",
          f"status={out['status']}")


def test_canonical_rendering_is_stable_and_type_faithful() -> None:
    check(execute._canonical([1, "a", True]) == execute._canonical([1, "a", True]),
          "canonical rendering is unstable")
    check(execute._canonical(()) == "t)", "empty tuple renders wrong")
    check(execute._canonical([]) == "l]", "empty list renders wrong")
    check(execute._canonical([1]) != execute._canonical((1,)),
          "list and tuple of the same contents render identically")
    check(execute._canonical(True) != execute._canonical(1),
          "bool and int render identically")
    # The collision that motivated type tags: without them a mutation turning
    # 1 into "1" would be invisible to the whole benchmark.
    check(execute._canonical(1) != execute._canonical("1"),
          "int and str render identically")
    check(execute._canonical(0) != execute._canonical(False),
          "zero and false render identically")


# --------------------------------------------------------------------------
# 8. The metric must respond to the method.
# --------------------------------------------------------------------------

def test_metric_responds_to_method_change() -> None:
    """The permanent negative control. If the score cannot tell a working
    method from a broken one, none of the numbers mean anything.

    Recomputed here rather than by running the whole benchmark, so it stays
    cheap enough to run every time."""
    for inst in inst_mod.load(GT_PATH):
        budget = evaluate._default_budget(inst)
        good = inst.oracle(budget)
        keep = methods.structure(inst, good)
        good_cost = good.spent + inst.set_cost(keep)
        good_det = inst.detect(keep)

        blind = inst.oracle(budget)
        all_probes = methods.keep_all(inst, blind)
        blind_cost = blind.spent + inst.set_cost(all_probes)
        blind_det = inst.detect(all_probes)

        check(blind_det == 1.0, "keep_all is not a safe reference", inst.module)
        check(good_det == 1.0,
              "shipped method does not detect everything on the shipped budget",
              f"{inst.module}: {good_det:.3f}")
        check(good_cost <= blind_cost,
              "shipped method costs more than the safe baseline",
              f"{inst.module}: {good_cost} > {blind_cost}")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            _failures.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
    print(f"{len(tests)} tests, {_checks} checks")
    if _failures:
        print(f"\n{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("all soundness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
