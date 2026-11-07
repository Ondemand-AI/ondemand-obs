"""The package declares requires-python >=3.9, so its source must parse and
import on 3.9 even though CI runs a newer interpreter.

Up to 0.1.14 ``__init__.py`` had three ``X | None`` annotations, so
``import ondemand_obs`` raised TypeError on 3.9 and took ondemand-ai's worker
down with it; a 3.10+ test run never notices. This test reads the source
instead of importing it, so it catches the 3.9 breakage on any interpreter.

Flagged, in annotations (evaluated at import time on 3.9, and evaluated by
``typing.get_type_hints``, which Temporal and dataclasses call, even under
``from __future__ import annotations``):

- PEP 604 unions: ``X | Y``
- PEP 585 builtin generics: ``list[int]``, ``dict[str, Any]``, ...

Flagged anywhere: ``match`` statements (3.10 syntax) and ``isinstance`` /
``issubclass`` with a ``X | Y`` second argument.
"""

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "ondemand_obs"

_BUILTIN_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}


def _offences_in_annotation(node: ast.AST):
    for sub in ast.walk(node):
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
            yield "PEP 604 union"
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id in _BUILTIN_GENERICS
        ):
            yield f"PEP 585 {sub.value.id}[...]"


def _annotations(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                if arg.annotation is not None:
                    yield arg.annotation
            for arg in (args.vararg, args.kwarg):
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def _offences(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for ann in _annotations(tree):
        for what in _offences_in_annotation(ann):
            yield f"{path.relative_to(PACKAGE.parent)}:{ann.lineno}: {what}"
    for node in ast.walk(tree):
        if type(node).__name__ == "Match":
            yield f"{path.relative_to(PACKAGE.parent)}:{node.lineno}: match statement"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("isinstance", "issubclass")
            and len(node.args) == 2
            and isinstance(node.args[1], ast.BinOp)
            and isinstance(node.args[1].op, ast.BitOr)
        ):
            yield f"{path.relative_to(PACKAGE.parent)}:{node.lineno}: {node.func.id} with X | Y"


def test_source_has_no_syntax_newer_than_python_39():
    offences = [o for path in sorted(PACKAGE.rglob("*.py")) for o in _offences(path)]
    assert offences == [], "Breaks Python 3.9 (use Optional/Union/List/Dict):\n" + "\n".join(offences)


def test_checker_catches_what_it_claims_to(tmp_path):
    """Guard the guard: a checker that matches nothing passes vacuously."""
    sample = tmp_path / "ondemand_obs" / "sample.py"
    sample.parent.mkdir()
    sample.write_text(
        "def f(a: str | None, b: list[int]) -> dict[str, int]:\n"
        "    return isinstance(a, int | str)\n",
        encoding="utf-8",
    )
    global PACKAGE
    saved, PACKAGE = PACKAGE, sample.parent
    try:
        found = list(_offences(sample))
    finally:
        PACKAGE = saved
    assert len(found) == 4, found
