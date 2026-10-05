"""Methods under evaluation.

Contract for every method: `fn(instance, oracle) -> list[int]` of probe indices.

A method is a *regression-set constructor*. It may run the real program a
limited number of times through `oracle`: each query is one full probe pass
over one candidate revision and costs accordingly. It must then commit to the
smallest set of probes that detects every mutant. Mutants it never queried
still count against it, so committing to a tiny set is not free.
"""

from __future__ import annotations

EPS = 1e-9


def _explore(instance, oracle, count: int) -> list:
    """Query the first `count` candidates in catalogue order. Baseline order."""
    seen = []
    for i in range(min(count, instance.n_mutants)):
        row = oracle.query(i)
        if row is not None:
            seen.append((i, row))
    return seen


def _explore_spread(instance, oracle, count: int, skip: int = 0) -> list:
    """Query candidates chosen by how much they can discriminate, not by
    catalogue order.

    Catalogue order is a poor sample: the candidates most likely to be visible
    are scattered through it. On one program only 6 of 55 candidates change any
    observation and the first of those sits at index 11, so a method that
    walks the list from the front spends its whole budget on candidates that
    cannot teach it anything.

    A selective candidate is an informative one. When a run moves a single
    probe, that probe is individually credited: keeping it is justified. When
    a run moves every probe at once, it says nothing about which of them is
    required, and the method is left keeping the lot. The predicted probe-set
    size is the free estimate of how selective a run will be, so candidates are
    taken smallest-prediction-first.

    Where every candidate predicts identically, prediction size cannot
    discriminate and the tie falls back to spreading over the source, which at
    least samples distinct regions of the program.

    `skip` resumes the ranking past candidates already run.
    """
    all_mutants = list(range(instance.n_mutants))
    if not all_mutants:
        return []

    sizes = {m: len(instance.predicts(m)) for m in all_mutants}
    distinct = len({frozenset(instance.predicts(m)) for m in all_mutants})
    if distinct > 1:
        order = sorted(all_mutants, key=lambda m: (sizes[m], m))
    else:
        order = sorted(all_mutants, key=lambda m: (instance.mutant_line(m), m))

    seen = []
    for m in order[skip:skip + count]:
        row = oracle.query(m)
        if row is not None:
            seen.append((m, row))
    return seen


def _rows(seen: list) -> list:
    return [row for _, row in seen]


def keep_all(instance, oracle) -> list[int]:
    """Baseline: never explore, keep every probe. Always detects; costs full."""
    return list(range(instance.n_probes))


def greedy_queried(instance, oracle) -> list[int]:
    """Baseline: explore, cover the queried mutants greedily. Ignores unqueried
    mutants, so it usually misses them -- the failure a real method must avoid."""
    seen = _explore(instance, oracle, oracle.remaining())
    if not seen:
        return list(range(instance.n_probes))
    return _greedy_cover(instance, _rows(seen), survivors=set())


def random_queried(instance, oracle) -> list[int]:
    """Baseline: explore, keep every probe that saw a queried change, shuffled.
    Isolates 'exploration helps' from 'selection logic helps'."""
    import random

    rows = _rows(_explore(instance, oracle, oracle.remaining()))
    if not rows:
        return list(range(instance.n_probes))
    chosen = [p for p in range(instance.n_probes) if any(r[p] for r in rows)]
    rng = random.Random(11)
    rng.shuffle(chosen)
    return sorted(chosen)


def _greedy_cover(instance, rows, survivors: set) -> list[int]:
    """Min-cost set cover of `rows`, cheapest-new-coverage first."""
    targets = list(rows) + [instance.row(m) for m in survivors]
    if not targets:
        return []
    chosen: list[int] = []
    uncovered = set(range(len(targets)))
    while uncovered:
        best, best_score = None, -1.0
        for p in range(instance.n_probes):
            if p in chosen:
                continue
            gain = sum(1 for m in uncovered if targets[m][p])
            if not gain:
                continue
            score = gain / max(1, instance.costs[p])
            if score > best_score + EPS:
                best, best_score = p, score
        if best is None:
            break
        chosen.append(best)
        uncovered = {m for m in uncovered if not targets[m][best]}
    return chosen


def structure(instance, oracle) -> list[int]:
    """SUBJECT METHOD: budget-filling predictive cover.

    What the measurements actually say:

    * A single well-chosen run is enough on some programs and not others. The
      best possible one run reaches detection 1.000 at ratio 0.064 on difflib
      and 0.255 on textwrap, but only 0.500 on fractions and cannot reach 1.000
      on shlex at all. So "run once and keep what fired" is not a method, it is
      a bet on the program.

    * No free static signal picks the right run. Call-graph centrality was
      measured and disproven: difflib's universal probe has centrality 0.24
      while the most central probe (0.79) detects only a quarter as much.

    * What does generalise is that a change inside function F is observed by
      the probes whose call closure contains F, and the union of those
      predictions covers every live mutation on three of four programs.

    So: spend the whole budget on runs spread across the source, and let the
    prediction carry the unrun part. Runs contribute measured facts; the
    prediction contributes coverage for everything else. If the runs reveal
    nothing, the budget bought no information and the honest answer is to keep
    everything -- having learned that before spending, not after.
    """
    predicted = {m: set(instance.predicts(m)) for m in range(instance.n_mutants)}

    # Before spending: can a run possibly change the answer? Only if the
    # predictions distinguish candidates. When every candidate predicts the same
    # probe set, no run can rule any of them out, so the budget would be spent
    # only to arrive at keep-everything. That degeneracy is visible for free
    # from the source, and it separates cleanly here: the programs where
    # exploring pays have 6 and 2 distinct predictions, the ones where it
    # cannot help have 1 each.
    if len({frozenset(p) for p in predicted.values()}) < 2:
        return list(range(instance.n_probes))

    seen = _explore_spread(instance, oracle, 1)
    observed: set = set()
    dead: set = set()
    for mutant, row in seen:
        real = {p for p, v in enumerate(row) if v}
        if real:
            observed |= real
        else:
            dead.add(mutant)

    if not observed:
        # The budget bought nothing. Do not pretend otherwise.
        return list(range(instance.n_probes))

    # A second run only earns its price if the first one discriminated. When a
    # single run moved every probe at once, it credited none of them
    # individually, and the answer is already "keep everything" -- so a second
    # run is not going to rescue it, it is just more money spent to arrive at
    # the same place. Spend it only when the first run actually narrowed
    # something down.
    if len(observed) < instance.n_probes:
        seen += _explore_spread(instance, oracle, 1, skip=len(seen))
        for mutant, row in seen[1:]:
            real = {p for p, v in enumerate(row) if v}
            if real:
                observed |= real
            else:
                dead.add(mutant)

    # Cover what the runs measured plus what static analysis predicts for every
    # candidate not proven invisible. Restricting to probes a run has seen fire
    # keeps the cover from buying evidence nobody has.
    targets = [{p for p, v in enumerate(row) if v} for _, row in seen]
    targets += [pred for m, pred in predicted.items() if m not in dead]

    chosen = _greedy_cover_sets(instance, targets, allowed=observed)
    chosen |= observed
    return sorted(chosen)


def _greedy_cover_sets(instance, targets: list, allowed: set = None) -> set:
    """Min-cost set cover over predicted detecting-probe sets.

    `allowed` restricts which probes may be selected, so a caller can demand
    that every kept probe has been seen observing something.
    """
    pool = sorted(allowed) if allowed is not None else list(range(instance.n_probes))
    chosen: set = set()
    remaining = [set(t) for t in targets if t]
    while remaining:
        best, best_score = None, -1.0
        for p in pool:
            if p in chosen:
                continue
            gain = sum(1 for t in remaining if p in t)
            if not gain:
                continue
            score = gain / max(1, instance.costs[p])
            if score > best_score + EPS:
                best, best_score = p, score
        if best is None:
            break
        chosen.add(best)
        remaining = [t for t in remaining if best not in t]
    return chosen


def _dominance_sweep(instance, rows, chosen) -> list[int]:
    """Replace kept probes by cheaper ones that observed-equivalently covered
    them and statically dominate them."""
    kept = set(chosen)
    improved = True
    while improved:
        improved = False
        for p in sorted(kept, key=lambda q: -instance.costs[q]):
            for q in range(instance.n_probes):
                if q == p or instance.costs[q] >= instance.costs[p]:
                    continue
                matched = all(any(r[q] for r in rows) for r in rows if any(r[p] for r in rows))
                if not matched:
                    continue
                if _statically_dominates(instance, q, p):
                    kept.discard(p)
                    kept.add(q)
                    improved = True
                    break
            if improved:
                break
    return sorted(kept)


def _statically_dominates(instance, q: int, p: int) -> bool:
    """q reaches every program function p reaches (and possibly more)."""
    sp, sq = set(instance.signatures[p]), set(instance.signatures[q])
    if not sp:
        return False
    return sp <= sq


def _insurance(instance, chosen) -> list[int]:
    """Keep the cheapest probe whose static call set no kept probe covers, so
    at least one observation lands in any region the cover is blind to."""
    kept = set(chosen)
    covered: set[str] = set()
    for p in kept:
        covered |= set(instance.signatures[p])
    for p in range(instance.n_probes):
        sig = set(instance.signatures[p])
        if sig and not sig <= covered:
            return sorted(kept | {p})
    return sorted(kept)
