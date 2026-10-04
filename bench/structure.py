"""Static signature of each probe: which program functions it exercises.

A probe's behaviour is determined by the code it calls. Two probes that call
overlapping sets of functions will tend to change together; two that call
disjoint sets will not. That relation is computable from the source alone, at
zero runtime cost, and it is what lets a method generalise from the mutants it
managed to run to the ones it never ran.

This is the piece prior work on trace compression does not use: it treats the
trace as a recorded sample. Here the trace is paired with a static map of the
program region each observation touches, so an unobserved mutant can be
predicted from a cheaper probe covering the same region.
"""

from __future__ import annotations

import ast


def function_table(source: str) -> dict[str, int]:
    """Qualified name -> line, for every function and class in the source.

    Classes are included as well as their methods, because a probe that builds
    a `Fraction` reaches its dunder methods without naming them.
    """
    tree = ast.parse(source)
    table: dict[str, int] = {}

    def visit(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                table[qual] = child.lineno
                visit(child, f"{qual}.")
            elif isinstance(child, ast.ClassDef):
                qual = f"{prefix}{child.name}"
                table[qual] = child.lineno
                visit(child, f"{qual}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return table


def _called_names(expr: ast.AST) -> set[str]:
    """Bare function names referenced anywhere in an expression."""
    names: set[str] = set()
    for node in ast.walk(expr):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _probe_fn(probe_src: str) -> ast.FunctionDef:
    """The probe function itself, not the helper preambles prepended to it."""
    tree = ast.parse(probe_src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__regpt_probe__":
            return node
    raise ValueError("no __regpt_probe__ in probe source")


def _alias_map(probe_src: str) -> dict[str, str]:
    """Resolve the probe's local aliases (`wrap = _m.wrap`) to the real
    attribute name, so signatures name program functions, not probe locals."""
    fn = _probe_fn(probe_src)
    aliases: dict[str, str] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        value = node.value
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            if value.value.id == "_m":
                for t in targets:
                    aliases[t] = value.attr
    return aliases


def probe_signature(probe_src: str, table: dict[str, int]) -> list[str]:
    """Which known program functions a probe's body reaches.

    Probe-local aliases are resolved first (`wrap = _m.wrap` -> `wrap`), then
    each name is matched against the function table by its bare name, since
    the table stores qualified paths (`textwrap.wrap`).
    """
    fn = _probe_fn(probe_src)
    aliases = _alias_map(probe_src)
    bare = {qual.rsplit(".", 1)[-1]: qual for qual in table}
    # Only the names in the *returned expression* count. The preamble binds
    # every alias the module offers, so walking the whole function would give
    # every probe an identical signature.
    returns = [n for n in fn.body if isinstance(n, ast.Return)]
    used: set[str] = set()
    for node in returns:
        used |= _called_names(node.value) if node.value is not None else set()
    resolved = {aliases.get(n, n) for n in used}
    return sorted(bare[n] for n in resolved if n in bare)


def similarity(a: list[str], b: list[str]) -> float:
    """Jaccard overlap of two probe signatures."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
