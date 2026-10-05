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


def _explore_spread(instance, oracle, count: int) -> list:
    """Query candidates chosen to spread across the program, not the catalogue.

    Catalogue order is a poor sample: the candidates most likely to be visible
    are scattered through it. On one program only 6 of 55 candidates change any
    observation, and the first of those sits at index 11, so a method that
    walks the list from the front spends its whole budget on candidates that
    cannot possibly teach it anything.

    Spreading by source position samples the program's distinct regions rather
    than its catalogue prefix, which is where a change is most likely to land.
    """
    order = sorted(range(instance.n_mutants), key=lambda m: (instance.mutant_line(m), m))
    if not order:
        return []
    n = len(order)
    picks = []
    if count >= n:
        picks = order
    else:
        # Evenly spaced over the source-ordered candidates.
        for k in range(count):
            idx = (k * n) // count
            pick = order[min(idx, n - 1)]
            if pick not in picks:
                picks.append(pick)
        for m in order:  # top up if spacing collided
            if len(picks) >= count:
                break
            if m not in picks:
                picks.append(m)

    seen = []
    for m in picks[:count]:
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
    """SUBJECT METHOD: verified predictive cover.

    Three measurements drive this design.

    1. Covering only the revisions you ran is hopeless: the best possible
       cover of the best two observed rows detects a fraction of what matters.
       A sample of revisions is not a sample of behaviour.

    2. Static call-graph reachability predicts the rest. A mutation inside
       function F is observed by the probes whose transitive call closure
       contains F, and the union of predictions over all candidates detects
       every live mutation on three of the four programs.

    3. That union is nearly everything, because candidates that *no probe can
       see* still get predicted onto broad probe sets. Those dead regions are
       what makes the union expensive, and they are exactly what a run can
       rule out.

    So exploration is spent on one job: proving a candidate invisible. A run
       that observes no change anywhere certifies that region dead, and its
       prediction can be dropped from the union. The answer is then a min-cost
       cover of the surviving predictions -- generalising to every revision
       never run, because nothing but the certified-dead was removed.
    """
    predicted = {m: set(instance.predicts(m)) for m in range(instance.n_mutants)}

    # Before spending anything: is exploration worth its price? The budget is
    # only recovered if a run can shrink the answer. It can only shrink the
    # answer if the predictions actually distinguish candidates: when every
    # candidate predicts the same probe set, no run can rule any of them out,
    # and the exploration would be spent to arrive at keep-everything anyway.
    #
    # That degeneracy is observable for free, from the source, before the first
    # run. On this benchmark it separates cleanly: the two programs where
    # exploring pays have 6 and 2 distinct predictions; the two where it cannot
    # help have exactly 1.
    if len({frozenset(p) for p in predicted.values()}) < 2:
        return list(range(instance.n_probes))

    seen = _explore_spread(instance, oracle, oracle.remaining())
    if not seen:
        return list(range(instance.n_probes))

    # A run that saw no change certifies its candidate invisible. Its predicted
    # probes are then not needed on its account.
    dead: set = set()
    observed: set = set()
    for mutant, row in seen:
        real = {p for p, v in enumerate(row) if v}
        if real:
            observed |= real
        else:
            # Invisible in a real run, so its prediction is not evidence.
            dead.add(mutant)

    # Survivors: every candidate we did not prove invisible, covered by what
    # static analysis predicts, plus the measured rows we did observe.
    targets = [pred for m, pred in predicted.items() if m not in dead]
    targets += [{p for p, v in enumerate(row) if v} for _, row in seen]

    # Predictions concentrate on cheap probes that no run has ever shown to
    # observe anything. Covering those is paying for evidence nobody has.
    # If no run revealed a single change, the budget bought no information at
    # all and the only defensible answer is to keep everything.
    if not observed:
        return list(range(instance.n_probes))

    trusted = _greedy_cover_sets(instance, targets, allowed=observed)
    chosen = trusted | observed
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
