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


def _explore(instance, oracle, count: int) -> list[list[int]]:
    """Query `count` candidate mutants. Returns the rows observed."""
    rows = []
    for i in range(min(count, instance.n_mutants)):
        row = oracle.query(i)
        if row is not None:
            rows.append(row)
    return rows


def keep_all(instance, oracle) -> list[int]:
    """Baseline: never explore, keep every probe. Always detects; costs full."""
    return list(range(instance.n_probes))


def greedy_queried(instance, oracle) -> list[int]:
    """Baseline: explore, cover the queried mutants greedily. Ignores unqueried
    mutants, so it usually misses them -- the failure a real method must avoid."""
    rows = _explore(instance, oracle, oracle.remaining())
    if not rows:
        return list(range(instance.n_probes))
    return _greedy_cover(instance, rows, survivors=set())


def random_queried(instance, oracle) -> list[int]:
    """Baseline: explore, keep every probe that saw a queried change, shuffled.
    Isolates 'exploration helps' from 'selection logic helps'."""
    import random

    rows = _explore(instance, oracle, oracle.remaining())
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
    """SUBJECT METHOD: regression-probe minimisation by structural transfer.

    A method that only covers the mutants it ran cannot know what it needs for
    the mutants it never ran. Delta-debugging style reduction asks "is this
    still needed *for the traces I have*"; this asks "will it still be needed
    for a revision I have never run", using the program's static call
    structure as the transfer function.

    Transfer argument: if probe p dominated q on every observed mutant, and p
    statically reaches everything q reaches, then any change that moves q also
    moves p, so p can replace q. This is the part no trace-compression method
    has, because they never look at what the trace touches in the program.
    """
    rows = _explore(instance, oracle, oracle.remaining())
    if not rows:
        return list(range(instance.n_probes))

    chosen = _greedy_cover(instance, rows, survivors=set())
    chosen = _dominance_sweep(instance, rows, chosen)
    chosen = _insurance(instance, chosen)
    return sorted(set(chosen))


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
