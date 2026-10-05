"""The instance a method sees, and the query oracle it is allowed to use.

Framing, in the terms the problem actually has:

* a program under test has a catalogue of *observations* (probes) with a
  declared price each;
* something changed the program and you need to know whether the change is
  *visible* in what you observe;
* you may go run the program a limited number of times before you commit to a
  regression set.

So a method spends a *query budget* on exploration, then emits a probe set.
Total cost is exploration plus the emitted set.

Two constants define the regime, and both are named rather than hidden:

`QUERY_COST_FRAC` -- price of running one candidate revision, as a fraction of
one full probe pass. The regression suite is already built and instrumented, so
a second revision is mostly fixed overhead, not a second full run. 0.25 means
four candidate revisions cost about one suite run.

`EXPORE_FRAC` (in evaluate.py) -- the total exploration allowance, as a multiple
of one full probe pass.

The pairing matters: if exploration is priced at a full pass *and* the budget
is a full pass, then a single query can consume the entire budget and the
oracle upper bound on the final cost ratio is ~0.92, so no method can beat
`keep_all` and the benchmark measures nothing. Pricing a revision at a quarter
of a pass and granting a full pass of budget gives ~4-16 queries, which is the
regime where generalisation is the binding constraint.
"""

from __future__ import annotations

import json
import os

# Price of running one candidate revision, as a fraction of a full probe pass.
QUERY_COST_FRAC = 0.25


class Oracle:
    """Budgeted access to ground-truth mutant rows.

    `query(i)` returns the measured influence row of mutant i: which probes see
    the change. A query costs `query_cost` (see `QUERY_COST_FRAC`), not a free
    peek, so a method cannot enumerate every revision and cheat.

    Candidates are presented as opaque ids: a method that could tell live from
    dead mutants without running them would skip the whole problem. Every query
    against a dead mutant still costs, which is the exploration risk a real
    engineer pays when running a candidate revision to see if it matters.
    """

    def __init__(self, influence, live, n_probes, budget, all_mutants, query_cost):
        self._influence = influence
        self._live = set(live)
        self._n_probes = n_probes
        self.budget = budget
        self.query_cost = query_cost
        self.used = 0
        self.queried: list[int] = []
        self.all_mutants = list(all_mutants)

    @property
    def spent(self) -> int:
        return self.used * self.query_cost

    def remaining(self) -> int:
        """How many more candidate revisions are affordable."""
        return max(0, (self.budget - self.spent) // max(1, self.query_cost))

    def query(self, i: int):
        """Influence row of mutant i, or None if unaffordable."""
        if self.remaining() < 1 or i not in self._all_set:
            return None
        self.used += 1
        self.queried.append(i)
        return list(self._influence[i])

    @property
    def _all_set(self) -> set:
        if not hasattr(self, "_all_set_cache"):
            self._all_set_cache = set(self.all_mutants)
        return self._all_set_cache

    def is_live(self, i: int) -> bool:
        """Whether a queried mutant actually changes anything. Only known after
        a query; a method cannot peek."""
        return i in self._live

    def live_count(self) -> int:
        return len(self._live)

    def n_probes(self) -> int:
        return self._n_probes


class Instance:
    """One program's data, minus the answer."""

    def __init__(self, module: str, costs: list[int], n_probes: int, live: list[int],
                 influence, n_mutants: int, mutants: list[str], signatures: list[list[str]]):
        self.module = module
        self.costs = costs
        self.n_probes = n_probes
        self._live = live
        self._influence = influence
        self.n_mutants = n_mutants
        self.mutants = mutants
        self.signatures = signatures

    def similar(self, a: int, b: int) -> float:
        """Static overlap of two probes' call sets, 0..1."""
        sa, sb = set(self.signatures[a]), set(self.signatures[b])
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def oracle(self, budget: int) -> Oracle:
        query_cost = max(1, int(self.full_cost() * QUERY_COST_FRAC))
        return Oracle(self._influence, self._live, self.n_probes, budget,
                      range(self.n_mutants), query_cost)

    def live(self) -> list[int]:
        return list(self._live)

    def row(self, i: int) -> list[int]:
        """Ground-truth row. Scoring only -- a method must not call this."""
        return list(self._influence[i])

    def detect(self, chosen: list[int]) -> float:
        """Fraction of live mutants visible in the chosen probes."""
        if not self._live:
            return 1.0
        chosen_set = set(chosen)
        hit = 0
        for m in self._live:
            row = self._influence[m]
            if any(row[p] for p in chosen_set):
                hit += 1
        return hit / len(self._live)

    def set_cost(self, chosen: list[int]) -> int:
        return sum(self.costs[p] for p in set(chosen))

    def full_cost(self) -> int:
        return sum(self.costs)


def load(path: str) -> list[Instance]:
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for module, p in data["programs"].items():
        influence = p["influence"]
        live = [i for i, mid in enumerate(p["mutants"]) if mid not in p["undetectable"]]
        out.append(
            Instance(
                module=module,
                costs=p["probe_costs"],
                n_probes=p["n_probes"],
                live=live,
                influence=influence,
                n_mutants=p["n_mutants"],
                mutants=p["mutants"],
                signatures=p.get("probe_signatures", []),
            )
        )
    return out


def default_budget(instance: Instance) -> int:
    """Exploration allowance: a fixed fraction of a full run per live mutant.

    Fixed fraction, not a fixed number, so the benchmark scales with instance
    size instead of handing small instances a free oracle.
    """
    return max(1, (len(instance.live()) * instance.full_cost()) // 20)
