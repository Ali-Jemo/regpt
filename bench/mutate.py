"""Deterministic mutation catalogue over real Python sources.

AST-level rewrite via ast.unparse, so mutants are real, syntactically valid
program variants rather than textual mangles. Catalogue is a pure function of
(source bytes, seed, cap) so the benchmark is byte-identical across runs.
"""

from __future__ import annotations

import ast
import hashlib
import random
from dataclasses import dataclass

BIN_FLIP = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
}
CMP_FLIP = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
}
BOOL_FLIP = {ast.And: ast.Or, ast.Or: ast.And}


@dataclass(frozen=True)
class Mutant:
    ident: str
    op: str
    line: int
    before: str
    after: str


def _fns(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _candidates(tree):
    """(key, op, node) triples that are safe to rewrite."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            if op in CMP_FLIP:
                out.append(((node.lineno, node.col_offset), "cmp", node))
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_FLIP:
            # floor-div on a float literal is not a faithful flip
            if any(isinstance(x, ast.Constant) and isinstance(x.value, float)
                   for x in ast.walk(node)):
                continue
            out.append(((node.lineno, node.col_offset), "binop", node))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            out.append(((node.lineno, node.col_offset), "not", node))
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL_FLIP:
            out.append(((node.lineno, node.col_offset), "boolop", node))
        elif (isinstance(node, ast.Constant) and node.value is True) or (
            isinstance(node, ast.Constant) and node.value is False
        ):
            out.append(((node.lineno, node.col_offset), "const", node))
        elif isinstance(node, ast.IfExp):
            out.append(((node.lineno, node.col_offset), "ifexp", node))
    out.sort(key=lambda t: t[0])
    return out


def _locate(tree, key):
    best = None
    for node in ast.walk(tree):
        pos = getattr(node, "lineno", None)
        if pos is None:
            continue
        if (pos, node.col_offset) == key:
            if best is None or type(node).__name__ == type(best).__name__:
                best = node
    return best


def _replace(tree, target, new):
    for field, value in ast.iter_fields(tree):
        if value is target:
            setattr(tree, field, new)
            ast.copy_location(new, target)
            return True
        if isinstance(value, list):
            for i, item in enumerate(value):
                if item is target:
                    value[i] = new
                    ast.copy_location(new, target)
                    return True
                if isinstance(item, ast.AST) and _replace(item, target, new):
                    return True
    return False


def build(path: str, seed: int, cap: int):
    """Return (mutants, {ident: source}) for one program."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    cands = _candidates(tree)
    if not cands:
        return [], {}
    rng = random.Random(seed)
    picks = list(range(len(cands)))
    rng.shuffle(picks)
    picks = sorted(picks[:cap])

    mutants, sources = [], {}
    for idx in picks:
        key, op, node = cands[idx]
        before = ast.unparse(node)
        new = ast.parse(before, mode="eval").body
        if op == "cmp":
            new.ops = [CMP_FLIP[type(node.ops[0])]()]
        elif op == "binop":
            new.op = BIN_FLIP[type(node.op)]()
        elif op == "not":
            new = ast.parse(before, mode="eval").body  # UnaryOp(Not(x)) -> x
        elif op == "boolop":
            new.op = BOOL_FLIP[type(node.op)]()
        elif op == "const":
            new.value = not node.value
        elif op == "ifexp":
            new.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        after = ast.unparse(new)
        if after == before:
            continue
        mutated = ast.parse(src)
        target = _locate(mutated, key)
        if target is None or not _replace(mutated, target, new):
            continue
        try:
            msrc = ast.unparse(mutated)
            ast.parse(msrc)
        except (ValueError, SyntaxError, RecursionError):
            continue
        ident = f"m{idx:03d}_{op}_L{key[0]}"
        mutants.append(Mutant(ident, op, key[0], before, after))
        sources[ident] = msrc
    return mutants, sources


def fingerprint(paths, seed, cap) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(open(p, "rb").read())
    h.update(f"|seed={seed}|cap={cap}|v3".encode())
    return h.hexdigest()[:16]
