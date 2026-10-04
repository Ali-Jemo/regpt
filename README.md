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

Two ingredients:

1. **Exploration** — a budgeted number of full probe passes over candidate
   revisions. Each pass costs the full probe set, so the budget is small by
   construction.
2. **Structural transfer** — the set of program functions each probe reaches,
   computed from the source at zero runtime cost. If probe `p` dominated `q`
   on every observed mutation and `p` statically reaches everything `q`
   reaches, then any change that moves `q` also moves `p`: `q` is droppable.

Delta debugging and test-suite minimisation answer "is this needed for the
traces I have". This answers "will it be needed for a revision I never ran",
which is the question a regression suite actually has to answer.

## Layout

    bench/programs/     real pure-Python stdlib sources (difflib, textwrap,
                        fractions, shlex), vendored unmodified
    bench/probes.py     real public-API calls, as deterministic source
    bench/mutate.py     AST-level mutation catalogue (deterministic)
    bench/structure.py  static call-set signature of each probe
    bench/execute.py    isolated execution oracle (fork per observation)
    bench/ground_truth.py  builds the influence matrix by real execution
    bench/instance.py   the query oracle and scoring
    bench/methods.py    baselines + the subject method
    bench/run_bench.py  scoring, emits METRIC lines

## Ground truth

No labels, no heuristics: every probe is run against every mutant in a forked
child, and the influence entry is whether the observation actually changed.
The matrix is the measured behaviour of real code.

## Metrics

`METRIC cost_ratio` — total declared cost (exploration + kept probes) as a
fraction of keeping every probe. **Lower is better. 1.0 = keep everything.**
An instance where detection is not 1.0 is charged the safe fallback of 1.0, so
a method cannot win by detecting less.

`METRIC detection` — fraction of live mutants caught. Must reach 1.0 to be
useful; reported for diagnosis.

## Running

    bash autoresearch.sh
