"""Budget and reporting for method evaluation.

A method is a callable `fn(instance, oracle) -> list[int]` returning the probe
set it commits to. It pays for every oracle query it makes and for the probes
it keeps. Detection must reach 1.0 to be useful; cost is then minimised.

Cost is reported as a fraction of the full probe set, so instances of
different sizes are comparable, and lower is better.
"""

from __future__ import annotations

import importlib

# Exploration allowance, as a multiple of the full probe-set cost.
#
# Chosen so the trade-off is real rather than decorative. Measured oracle
# bound (a method that sees the entire influence matrix, the best possible
# outcome at this budget):
#   EXPORE_FRAC=0.5 -> 0.657   2 queries   <- selected
#   EXPORE_FRAC=1.0 -> 1.163   4 queries   (exploration alone exceeds the suite)
#   EXPORE_FRAC=2.0 -> 2.163   8 queries
# At 0.5 a method that generalises properly can reach well under keep_all's
# 1.0, and one that does not generalise is still caught by the detection
# constraint. Above 1.0 exploration alone costs more than the whole suite, so
# keep_all wins by construction and the benchmark measures nothing.
EXPORE_FRAC = 0.5


def _default_budget(instance) -> int:
    return max(instance.n_probes, int(instance.full_cost() * EXPORE_FRAC))


def load_method(spec: str):
    """`module:function` -> callable."""
    mod_name, fn_name = spec.split(":")
    mod = importlib.import_module(mod_name)
    importlib.reload(mod)
    return getattr(mod, fn_name)


def report(name: str, rows: list[dict]) -> str:
    lines = [f"  {name}:"]
    tot_ratio = 0.0
    for row in rows:
        flag = "OK " if row["detect"] >= 1.0 else "MISS"
        lines.append(
            f"    {row['module']:10s} {flag} ratio={row['ratio']:5.3f} "
            f"cost={row['cost']:5d} explore={row['explore']:5d} keep={row['keep']:5d} "
            f"q={row['queries']:3d} detect={row['detect']:.3f}"
        )
        tot_ratio += row["ratio"]
    lines.append(f"    MEAN ratio={tot_ratio / len(rows):.4f}")
    return "\n".join(lines)
