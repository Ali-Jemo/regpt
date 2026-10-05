"""Call-graph reachability: which program functions a probe actually exercises.

A probe that calls `wrap` does not just exercise `wrap`, it exercises every
function `wrap` can reach. That transitive closure is the real static footprint
of an observation, and it is what a method needs to predict whether an
unexplored mutation will move a probe it has never run.

The earlier signature (direct name references only) was too coarse to transfer:
a probe calling `wrap` and one calling `fill` looked identical even when they
share almost no code. Reachability separates them.
"""

from __future__ import annotations

import ast


def qualified_functions(source: str) -> dict[str, ast.AST]:
    """Qualified name -> node, for every function and class."""
    tree = ast.parse(source)
    table: dict[str, ast.AST] = {}

    def visit(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                table[qual] = child
                visit(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                qual = f"{prefix}{child.name}"
                table[qual] = child
                visit(child, f"{qual}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return table


def _call_names(node: ast.AST) -> set[str]:
    """Bare names of every call made inside `node`."""
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            fn = child.func
            if isinstance(fn, ast.Name):
                out.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                out.add(fn.attr)
    return out


def _class_of(qual: str) -> str | None:
    parts = qual.split(".")
    for i, part in enumerate(parts):
        if i and part[:1].isupper():
            return ".".join(parts[: i + 1])
    return None


def build_caller_map(functions: dict[str, ast.AST]) -> dict[str, set[str]]:
    """For each function, the functions it can transitively reach.

    Edges are resolved by bare name, which is exact for a single module (the
    programs benchmarked are all single-module stdlib files) and a safe
    over-approximation otherwise: a name that is ambiguous resolves to every
    candidate, so reachability is never under-reported.
    """
    by_bare: dict[str, list[str]] = {}
    for qual in functions:
        by_bare.setdefault(qual.rsplit(".", 1)[-1], []).append(qual)

    direct: dict[str, set[str]] = {}
    for qual, node in functions.items():
        targets: set[str] = set()
        called = _call_names(node)
        # A method on a class may also be reached through the class itself.
        klass = _class_of(qual)
        if klass and klass in functions:
            called |= _call_names(functions[klass])
        for name in called:
            for cand in by_bare.get(name, ()):
                if cand != qual:
                    targets.add(cand)
        direct[qual] = targets

    # Transitive closure by fixpoint. Small graphs, so this is cheap and exact.
    reach = {k: set(v) for k, v in direct.items()}
    changed = True
    while changed:
        changed = False
        for qual in list(reach):
            grown = set(reach[qual])
            for t in reach[qual]:
                grown |= reach.get(t, set())
            if grown != reach[qual]:
                reach[qual] = grown
                changed = True
    return reach


def probe_reach(probe_src: str, functions: dict[str, ast.AST]) -> set[str]:
    """Transitive closure of the program functions a probe exercises."""
    tree = ast.parse(probe_src)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "__regpt_probe__"
    )
    by_bare: dict[str, list[str]] = {}
    for qual in functions:
        by_bare.setdefault(qual.rsplit(".", 1)[-1], []).append(qual)

    called: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                called.add(target.id)
            elif isinstance(target, ast.Attribute):
                called.add(target.attr)
        elif isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Call):
                    target = sub.func
                    if isinstance(target, ast.Name):
                        called.add(target.id)
                    elif isinstance(target, ast.Attribute):
                        called.add(target.attr)

    # A probe may reach the program through a helper defined alongside it, so
    # following only the probe's own calls would silently report "reaches
    # nothing" for every helper-based probe. Chasing the local helper's own
    # calls is what makes those probes visible to the predictor at all.
    local = {
        n.name: n for n in ast.parse(probe_src).body
        if isinstance(n, ast.FunctionDef) and n.name != "__regpt_probe__"
    }
    pending = list(called)
    while pending:
        name = pending.pop()
        helper = local.get(name)
        if helper is None:
            continue
        for node in ast.walk(helper):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            hit = target.id if isinstance(target, ast.Name) else (
                target.attr if isinstance(target, ast.Attribute) else None
            )
            if hit and hit not in called:
                called.add(hit)
                pending.append(hit)

    reach = build_caller_map(functions)
    seeds: set[str] = set()
    for name in called:
        seeds |= set(by_bare.get(name, ()))
    out: set[str] = set()
    for s in seeds:
        out.add(s)
        out |= reach.get(s, set())
    return out


def function_of_line(functions: dict[str, ast.AST], line: int) -> str | None:
    """The function (or class) whose body contains `line`."""
    best, best_end = None, -1
    for qual, node in functions.items():
        start = getattr(node, "lineno", 0)
        if start > line:
            continue
        end = getattr(node, "end_lineno", start) or start
        if start <= line <= end and end > best_end:
            best, best_end = qual, end
    return best
