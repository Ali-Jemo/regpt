# regpt — regression-probe minimisation

## The problem

A program under test has a set of **probes** (tests, assertions, logged
observations). Each probe has a runtime cost. You want the smallest set of
probes that still **detects any behaviour change** — a regression set that is
cheap to run forever.

The expensive part is knowing which changes are detectable. You cannot know
without running the program, and running the whole suite on every candidate
revision is precisely the cost you are trying to avoid.

So the question is:

> Given a probe set, a price per probe, and a **budget for exploratory runs**,
> find the cheapest probe set that detects every behaviour change — including
> the changes you never got to run.

## Why the last clause is the real problem

A method that only keeps what it saw during exploration will miss anything it
did not explore. `keep_all` (keep every probe) is always safe and always costs
1.0. Any method that beats 1.0 has to **generalise from the mutations it ran to
the mutations it did not** — and the only free signal for that is the program's
own static structure.

## The method

1. **Predict.** For every candidate revision, static call-graph reachability
   says which probes would observe it: a change inside function `F` is seen by
   the probes whose transitive call closure contains `F`. This is computed from
   the source at zero runtime cost, and it is the generalisation signal — it
   predicts a revision the method never ran.

2. **Gate before spending.** Exploring only pays if some run can *discriminate*:
   move a few probes rather than all of them, so each moving probe is
   individually credited. If every candidate is predicted to move everything —
   or is predicted to be seen by nothing, which is not the same as being
   predicted sharp — no run can single a probe out, and the honest answer is to
   keep everything without spending.

3. **Run smallest-prediction-first**, preferring kinds of change already seen to
   move. A run that moves one probe credits it; a run that moves all sixteen
   credits none.

4. **Stop when a selective run has settled it.** If the observed set is a
   minority of the probes and strictly cheaper than the whole suite, another run
   cannot improve on individually-credited evidence.

5. **Cover** the observed rows plus the predictions of every candidate not
   proven invisible, restricted to probes a run has actually seen fire.

Delta debugging and test-suite minimisation answer "is this needed for the
traces I have". This answers "will it be needed for a revision I never ran",
which is the question a regression suite actually has to answer.

## Results

| method | cost_ratio | detection |
|---|---|---|
| `keep_all` | 1.000 | 1.000 |
| `greedy_queried` | 0.761 | 0.594 (infeasible) |
| **`structure`** | **0.885** | **1.000** |

Per instance: difflib 0.308, textwrap 1.000, fractions 1.000, shlex 1.000,
configparser 1.000, fnmatch 1.000. Flat across the budget sweep (0.25x / 1x /
2x), so the result is not an artefact of one exploration level. Deterministic
across repeated runs and across a clean ground-truth rebuild.

## The ceiling: liveness is not knowable from source

Pooled over all 240 candidates in six programs, single-feature AUC for telling
a *live* candidate (one any probe observes) from a *dead* one:

| feature | AUC |
|---|---|
| predicted probe-set size | 0.279 (anti-correlated) |
| enclosing function exists | 0.504 (chance) |
| **source line** | **0.640** |

The best free signal is mediocre and the obvious one is inverted. Liveness is
a property of whether the mutated code path is *executed* with a probe's
particular arguments — dynamic by construction. Every selection heuristic in the
method is a proxy for liveness, which is why fourteen of them were tried and
the useful one is the gate that declines to act.

This also explains the per-instance pattern. Only `fractions` is genuinely
unwinnable (16 of its 22 probes never discriminate); the other five are
reachable at 0.064–0.256 by an oracle. The method captures difflib, and the
rest sit at 1.0 because the measurements show exploring there loses:

| policy | outcome when the first run is dead |
|---|---|
| gate (current) | 1.000 |
| always explore | 1.23–1.25 |

Four of six programs have a dead first run under every selector tried, so
declining to spend *is* the win on those instances.

## Prior art

Adjacent work is mutation-based test selection — FASE 2012 "Reduction of Test
Suites Using Mutation", ICST 2018 "Speeding up Mutation Testing via Regression
Test Selection", "Mutant Reduction Evaluation" (2022) — which takes the mutant as
input and selects tests for it. Here candidates are never run, so the detecting
set must be predicted from source alone. Delta debugging (2002) and PASTE greedy
suite minimisation (2005) share the cover step but not the prediction.

## Layout

    bench/programs/     real pure-Python stdlib sources (difflib, textwrap,
                        fractions, shlex, configparser, fnmatch), vendored
                        unmodified
    bench/probes.py     real public-API calls, as deterministic source
    bench/mutate.py     AST-level mutation catalogue (deterministic)
    bench/callgraph.py  transitive call closure + line-to-function mapping
    bench/structure.py  direct-name probe signatures (comparison baseline)
    bench/execute.py    isolated execution oracle (fork per observation)
    bench/ground_truth.py  builds the influence matrix by real execution
    bench/instance.py   the query oracle and scoring
    bench/evaluate.py   exploration budget and reporting
    bench/methods.py    baselines + the subject method
    bench/run_bench.py  scoring, emits METRIC lines
    tests/test_soundness.py  is the harness trustworthy? (gates the run)
    tests/test_behaviour.py  does it behave as documented? (gates the run)

## Ground truth

No labels, no heuristics: every probe is run against every mutant in a forked
child, and the influence entry is whether the observation actually changed.
The matrix is the measured behaviour of real code. The cache is fingerprinted
over the program sources *and* the probe definitions, so an edited probe set
forces a rebuild rather than silently serving a stale matrix.

## Tests

    python3 tests/test_soundness.py     # is this harness trustworthy?
    python3 tests/test_behaviour.py     # does it behave as documented?

36 tests, ~6,300 assertions, ~90s. Both run as stage 1 of `autoresearch.sh` and
gate it: a soundness failure exits 1 and no benchmark result is printed.

The split matters. *Soundness* asks whether the number means anything; *behaviour*
asks whether the method and the scoring do what they claim. A harness can be
perfectly sound and still score the wrong thing.

Every soundness test corresponds to a bug that actually occurred while building
this, because a test that cannot fail is worse than no test — it is believed:

| test | bug it catches |
|---|---|
| subject method reads ground truth | a method peeking at the influence matrix scores perfectly and proves nothing |
| oracle bills every query | exploration becoming free |
| query cost is a realistic fraction of the suite | the phase-1 model that made `keep_all` unbeatable by construction |
| fingerprint covers probes and the executor | an edited probe set or a changed observation encoding silently serving a stale matrix |
| every probe returns a value | five probes that had been raising since they were written |
| probe reach is never spuriously empty | a call-graph resolver reporting three working probes as reaching nothing |
| canonical rendering is type-faithful | `1` and `"1"` rendering identically, so a mutation changing one to the other would be invisible |
| metric responds to method change | a score that cannot tell a working method from a broken one |

The last is a permanent negative control: forcing the method to `keep_all` must
move the score to 1.000, and injecting a ground-truth peek must fail the gate.

## Known defect

None outstanding. The five probes that had been raising since they were written
are fixed: `ndif` was a typo for `ndiff` and returned a generator that had to be
consumed; `commenters` was passed as a constructor keyword when `shlex.__init__`
takes no such argument; and two error-path probes were propagating exceptions
instead of capturing them, so a mutation that stopped the library rejecting bad
input could not be seen. All 101 probes now return a value.

## Metrics

`METRIC cost_ratio` — total declared cost (exploration + kept probes) as a
fraction of keeping every probe. **Lower is better. 1.0 = keep everything.**
An instance where detection is not 1.0 is charged the safe fallback of 1.0, so
a method cannot win by detecting less.

`METRIC detection` — fraction of live mutants caught. Must reach 1.0 to be
useful; reported for diagnosis.

`METRIC cost_ratio_at_*` — the same metric at other exploration budgets, so
regime sensitivity is visible rather than hidden behind one constant.

## Running

    bash autoresearch.sh
