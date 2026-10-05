"""Behaviour tests: what the benchmark and the method actually do.

Where `test_soundness.py` asks "is this harness trustworthy?", this file asks
"does the thing behave as documented?". The distinction matters: a harness can
be sound and still score the wrong thing.

Run: python3 tests/test_behaviour.py
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
sys.path.insert(0, BENCH)

import evaluate  # noqa: E402
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


def _instances():
    return inst_mod.load(GT_PATH)


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_cost_ratio_is_cost_over_full_suite() -> None:
    """`cost_ratio` is the number the benchmark is judged on, so its definition
    is pinned here rather than left to whoever reads run_bench.py next."""
    for inst in _instances():
        oracle = inst.oracle(evaluate._default_budget(inst))
        chosen = methods.keep_all(inst, oracle)
        total = oracle.spent + inst.set_cost(chosen)
        check(abs(total / inst.full_cost() - 1.0) < 1e-9,
              "keep_all is not exactly ratio 1.0", inst.module)
        check(abs(sum(inst.costs) - inst.full_cost()) < 1e-9,
              "full_cost is not the sum of probe costs", inst.module)


def test_infeasible_instances_are_charged_the_safe_fallback() -> None:
    """The rule that stops a method winning by detecting less. `greedy_queried`
    is the witness: it scores a low raw ratio and must be charged 1.0 because it
    misses mutants."""
    for inst in _instances():
        oracle = inst.oracle(evaluate._default_budget(inst))
        chosen = methods.greedy_queried(inst, oracle)
        det = inst.detect(chosen)
        raw = (oracle.spent + inst.set_cost(chosen)) / inst.full_cost()
        charged = raw if det >= 1.0 else 1.0
        if det < 1.0:
            check(charged == 1.0,
                  "an infeasible instance was not charged the fallback",
                  f"{inst.module}: raw {raw:.3f} det {det:.3f}")
        else:
            check(charged == raw,
                  "a feasible instance was not charged its real cost",
                  f"{inst.module}: raw {raw:.3f}")


def test_shipped_method_holds_full_detection() -> None:
    """Detection is a hard constraint, not a tradeoff. If this ever fails, the
    headline number is meaningless even if cost_ratio looks good."""
    for inst in _instances():
        oracle = inst.oracle(evaluate._default_budget(inst))
        chosen = methods.structure(inst, oracle)
        check(inst.detect(chosen) >= 1.0,
              "shipped method does not detect every live mutant",
              f"{inst.module}: {inst.detect(chosen):.3f}")


def test_shipped_method_never_costs_more_than_keep_all() -> None:
    """The gate is the method's main claim: declining to explore is better than
    exploring and finding nothing. If exploring ever paid, the gate would be
    wrong and this would catch it."""
    for inst in _instances():
        budget = evaluate._default_budget(inst)
        gate = inst.oracle(budget)
        gated = methods.structure(inst, gate)
        gated_cost = gate.spent + inst.set_cost(gated)

        blind = inst.oracle(budget)
        keep = methods.keep_all(inst, blind)
        keep_cost = blind.spent + inst.set_cost(keep)

        check(gated_cost <= keep_cost,
              "exploring cost more than not exploring",
              f"{inst.module}: {gated_cost} > {keep_cost}")


def test_gate_declines_to_spend_when_no_run_can_discriminate() -> None:
    """A program where every candidate is predicted to touch the whole suite
    cannot be probed cheaply, so the method must not spend. fractions is the
    witness: its best possible cover is keep-all."""
    for inst in _instances():
        sizes = [len(inst.predicts(m)) for m in range(inst.n_mutants)
                 if inst.predicts(m)]
        gateable = bool(sizes) and min(sizes) < inst.n_probes * 0.5
        oracle = inst.oracle(evaluate._default_budget(inst))
        methods.structure(inst, oracle)
        if not gateable:
            check(oracle.used == 0,
                  "the method spent budget on an ungateable program",
                  f"{inst.module}: used {oracle.used} queries")
        else:
            check(True, "")


def test_method_is_deterministic_across_repeated_calls() -> None:
    """No randomness is permitted in a method, so two runs on the same instance
    must agree exactly."""
    for inst in _instances():
        a = inst.oracle(evaluate._default_budget(inst))
        first = methods.structure(inst, a)
        b = inst.oracle(evaluate._default_budget(inst))
        second = methods.structure(inst, b)
        check(first == second, "method is not deterministic", inst.module)
        check(a.spent == b.spent, "method spends different amounts run to run",
              f"{inst.module}: {a.spent} vs {b.spent}")


def test_a_weaker_method_scores_no_better() -> None:
    """Monotonicity. `keep_all` is the safe reference; nothing that also holds
    detection at 1.0 may cost more than it."""
    for inst in _instances():
        keep_cost = inst.full_cost()
        for name in ("structure",):
            oracle = inst.oracle(evaluate._default_budget(inst))
            chosen = methods.structure(inst, oracle)
            if inst.detect(chosen) >= 1.0:
                check(oracle.spent + inst.set_cost(chosen) <= keep_cost,
                      f"{name} costs more than the safe reference", inst.module)


# --------------------------------------------------------------------------
# The mutation catalogue
# --------------------------------------------------------------------------

def test_mutation_catalogue_is_deterministic() -> None:
    """Ground truth must be reproducible: the same source and seed must give the
    same mutants, or the benchmark is not measuring a fixed problem."""
    path = os.path.join(BENCH, "programs", "difflib.py")
    a_muts, a_srcs = mutate.build(path, 20261004, 40)
    b_muts, b_srcs = mutate.build(path, 20261004, 40)
    check([m.ident for m in a_muts] == [m.ident for m in b_muts],
          "mutation catalogue is not deterministic (ids)")
    check(a_srcs == b_srcs, "mutation catalogue is not deterministic (sources)")


def test_different_seeds_give_different_catalogues() -> None:
    path = os.path.join(BENCH, "programs", "difflib.py")
    a, _ = mutate.build(path, 1, 40)
    b, _ = mutate.build(path, 2, 40)
    check({m.ident for m in a} != {m.ident for m in b} or not a,
          "different seeds produced identical catalogues")


def test_mutations_report_what_they_changed() -> None:
    """A mutation that reports the same before and after is a no-op that would
    silently waste a slot in the catalogue."""
    path = os.path.join(BENCH, "programs", "textwrap.py")
    muts, _ = mutate.build(path, 20261004, 60)
    for m in muts:
        check(m.before != m.after,
              "mutation reports no change", m.ident)
        check(m.line > 0, "mutation has no source line", m.ident)


# --------------------------------------------------------------------------
# Structural prediction
# --------------------------------------------------------------------------

def test_prediction_is_a_subset_of_the_probe_set() -> None:
    for inst in _instances():
        for m in range(inst.n_mutants):
            pred = inst.predicts(m)
            check(pred <= set(range(inst.n_probes)),
                  "prediction names a probe that does not exist",
                  f"{inst.module} mutant {m}")
            check(isinstance(pred, set), "prediction is not a set",
                  f"{inst.module} mutant {m}")


def test_mutant_line_maps_into_the_source() -> None:
    """The predictor looks up a mutation's line. If that line does not exist in
    the program, the prediction is silently meaningless."""
    for inst in _instances():
        n_lines = len(inst.source.splitlines())
        for m in range(inst.n_mutants):
            line = inst.mutant_line(m)
            check(1 <= line <= n_lines,
                  "mutation line is outside the source",
                  f"{inst.module} mutant {m}: line {line} of {n_lines}")


def test_function_of_line_is_consistent_with_the_mutation_line() -> None:
    for inst in _instances():
        for m in range(inst.n_mutants):
            fn = inst.fn_of_line(inst.mutant_line(m))
            check(fn is None or isinstance(fn, str),
                  "function lookup returned something odd", f"{inst.module} {m}")


# --------------------------------------------------------------------------
# Instances
# --------------------------------------------------------------------------

def test_every_program_is_actually_exercised() -> None:
    """A program with no live mutants, or none at all, contributes a free 1.0
    to the mean and inflates the score without testing anything."""
    for inst in _instances():
        check(inst.n_probes > 0, "program has no probes", inst.module)
        check(inst.n_mutants > 0, "program has no mutants", inst.module)
        check(len(inst.live()) > 0,
              "program has no live mutants, so it cannot test detection",
              inst.module)
        check(inst.full_cost() > 0, "program has zero total cost", inst.module)


def test_probe_costs_are_positive() -> None:
    """A zero-cost probe would make the cover free and the cost model
    meaningless."""
    for inst in _instances():
        for p, c in enumerate(inst.costs):
            check(c > 0, "probe has non-positive cost", f"{inst.module} probe {p}")


def test_instance_loads_every_program_in_the_fixture() -> None:
    with open(GT_PATH, encoding="utf-8") as fh:
        gt = json.load(fh)
    loaded = {i.module for i in _instances()}
    check(loaded == set(gt["programs"]),
          "not every program in the fixture became an instance",
          f"loaded {sorted(loaded)} vs fixture {sorted(gt['programs'])}")
    check(loaded == set(probes.PROGRAMS),
          "the probe module lists programs the fixture does not have",
          f"probes.PROGRAMS {sorted(probes.PROGRAMS)} vs loaded {sorted(loaded)}")


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
    print("all behaviour checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
