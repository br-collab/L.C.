"""Import boundaries for the `lc` package (JUM-D-06).

The top-level Cannae Legion C2 (command and control) harness and the external
emulators are planned as sibling packages, `harness_c2` and `emulators`, in this
repository. The middle layer under test must never import either: if it could
see the harness or the emulators, an experiment could no longer show that L.C.
behaved independently of the thing measuring it (Research Charter §17.9).

Neither package exists yet, so these tests pass trivially today. They exist so
the rule is enforced from the first commit that adds one, rather than written
down and discovered broken later.
"""

import ast
import json
import pkgutil
import subprocess
import sys
from pathlib import Path

import lc

FORBIDDEN = ("harness_c2", "emulators")


def _is_forbidden(module_name: str) -> bool:
    root = module_name.split(".", 1)[0]
    return root in FORBIDDEN


def test_lc_imports_cleanly() -> None:
    assert lc.__version__


def test_importing_lc_loads_no_forbidden_package() -> None:
    # A fresh interpreter, so nothing another test imported can hide or fake a result.
    # Every submodule is imported, not only the package root.
    probe = (
        "import importlib, json, pkgutil, sys\n"
        "import lc\n"
        "for m in pkgutil.walk_packages(lc.__path__, 'lc.'):\n"
        "    importlib.import_module(m.name)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    loaded = json.loads(result.stdout)
    assert [name for name in loaded if _is_forbidden(name)] == []


def test_lc_source_names_no_forbidden_package() -> None:
    # Catches function-local imports that a runtime check only sees if the function runs.
    offenders = []
    package_dir = Path(lc.__file__).parent
    for path in sorted(package_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            offenders += [f"{path.name}:{node.lineno} {n}" for n in names if _is_forbidden(n)]
    assert offenders == []


def test_walk_covers_every_lc_module() -> None:
    # Guards the guard: if lc grows a subpackage the walk cannot reach, say so.
    walked = {m.name for m in pkgutil.walk_packages(lc.__path__, "lc.")}
    package_dir = Path(lc.__file__).parent
    on_disk = {
        "lc." + ".".join(p.relative_to(package_dir).with_suffix("").parts)
        for p in package_dir.rglob("*.py")
        if p.name != "__init__.py"
    }
    assert on_disk <= walked
