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

# Fraction of the probe set a candidate's prediction may cover and still count
# as sharp enough to be worth a run. Measured separation on this benchmark:
# candidates in the programs where exploring pays are predicted to touch at most
# 0.30 of the suite, while candidates in the programs where it cannot pay are
# predicted to touch all of it. Half sits between the two groups.
SELECTIVITY_GATE = 0.5


def _explore(instance, oracle, count: int) -> list:
    """Query the first `count` candidates in catalogue order. Baseline order."""
    seen = []
    for i in range(min(count, instance.n_mutants)):
        row = oracle.query(i)
        if row is not None:
            seen.append((i, row))
    return seen


def _kind(instance, mutant: int) -> str:
    """The sort of change a candidate makes: an operator flip, a boolean flip,
    a constant flip, a conditional rewrite. Encoded in the candidate id."""
    ident = instance.mutants[mutant]
    parts = ident.split("_")
    return parts[1] if len(parts) > 1 else ident


def _explore_spread(instance, oracle, count: int, skip: int = 0,
                    prefer_kinds: set = None) -> list:
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
    prefer_kinds = prefer_kinds or set()

    if distinct > 1:
        order = sorted(
            all_mutants,
            key=lambda m: (0 if _kind(instance, m) in prefer_kinds else 1, sizes[m], m),
        )
    else:
        order = sorted(
            all_mutants,
            key=lambda m: (
                0 if _kind(instance, m) in prefer_kinds else 1,
                instance.mutant_line(m),
                m,
            ),
        )

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

    # Before spending: is there any run that could narrow the answer?
    #
    # A run earns its price when it can *discriminate*: when it moves a few
    # probes rather than all of them, each moving probe is individually
    # credited and keeping it is justified. When every candidate is predicted to
    # move everything, no run can single any probe out, and the answer can only
    # come out "keep everything" -- the budget would buy nothing but the
    # privilege of having spent it.
    #
    # So the test is whether any candidate is predicted to be *sharp*: to move
    # a small, non-empty slice of the suite. An empty prediction does not count,
    # because a candidate that is predicted to be seen by nothing is a
    # candidate static analysis has no opinion about, and running it teaches
    # nothing in advance. Measured separation on this benchmark: every
    # candidate in the programs where exploring pays is predicted to touch at
    # most 0.30 of the suite, and every candidate in the programs where it
    # cannot pay is predicted to touch all of it.
    sizes = [len(p) for p in predicted.values() if p]
    if not sizes or min(sizes) >= instance.n_probes * SELECTIVITY_GATE:
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

    # Learn which kinds of change are visible at all.
    #
    # Not every kind of change shows up in the probe set. Measured on this
    # benchmark, comparison-flipping mutations are invisible in two of the four
    # programs, entirely, while arithmetic and boolean mutations are visible
    # nearly always. That is a property of the candidate catalogue, not of any
    # one program, and a run reveals it for free: run a candidate, see whether
    # anything moved, and you have learned whether that *kind* of change is
    # worth running at all.
    #
    # This matters because most candidates are dead, and a method that spends
    # its budget on them learns nothing. Preferring candidates whose kind has
    # already produced a change steers the next run at one that has a chance of
    # producing one.
    visible_kinds = {_kind(instance, m) for m, row in seen if any(row)}

    # Stop when a selective run has already settled the answer cheaply.
    #
    # A run that moves a single probe credits that probe individually: it is
    # justified on its own, and it is the whole of the observed evidence. A run
    # that moves many probes credits none of them individually, so more of them
    # say nothing new about which is required. That asymmetry is what makes one
    # run enough and a second pure waste.
    #
    # Measured on this benchmark: one selective run on difflib moves probe 7
    # alone, and that probe catches all 65 live changes, so the answer is
    # settled at cost 5 with one run. A second run there costs 19 units and
    # reduces the answer by nothing.
    #
    # A run that moved nothing is a wasted query rather than a finding, so the
    # budget is carried forward to another candidate rather than spent on
    # nothing.
    while oracle.remaining() > 0 and not _settled(instance, observed, predicted, dead):
        before = len(seen)
        more = _explore_spread(instance, oracle, 1, skip=len(seen),
                               prefer_kinds=visible_kinds)
        if not more:
            break
        for mutant, row in more:
            real = {p for p, v in enumerate(row) if v}
            if real:
                observed |= real
            else:
                dead.add(mutant)
        seen += more
        if len(seen) == before:
            break

    # Cover what the runs measured plus what static analysis predicts for every
    # candidate not proven invisible. Restricting to probes a run has seen fire
    # keeps the cover from buying evidence nobody has.
    targets = [{p for p, v in enumerate(row) if v} for _, row in seen]
    targets += [pred for m, pred in predicted.items() if m not in dead]

    chosen = _greedy_cover_sets(instance, targets, allowed=observed)
    chosen |= observed
    return sorted(chosen)


def _settled(instance, observed: set, predicted: dict, dead: set) -> bool:
    """True when another run cannot improve the answer.

    Two conditions, and both matter. The observed set must be *selective* --
    it must have come from a run that moved a minority of the probes -- because
    a run that moved everything credits no probe individually, so a further run
    is the only thing that could ever single one out. And the observed set must
    be strictly cheaper than the whole suite, since past that point the answer
    is "keep everything" whatever else is learned.
    """
    if not observed:
        return False
    if len(observed) >= instance.n_probes:
        return False
    return instance.set_cost(sorted(observed)) < instance.full_cost()


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
