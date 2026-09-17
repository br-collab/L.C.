"""Import boundaries for the `lc` and `cop` packages (JUM-D-06, COP-0).

The top-level Cannae Legion C2 (command and control) harness and the external
emulators are planned as sibling packages, `harness_c2` and `emulators`, in this
repository. The middle layer under test must never import either: if it could
see the harness or the emulators, an experiment could no longer show that L.C.
behaved independently of the thing measuring it (Research Charter §17.9).

`cop` (the COP-0 Common Operating Picture program picture) is also a sibling. It only
shows; it holds no authority. So `lc` never imports `cop`, and `cop` never imports `lc`,
the harness, the emulators or a domain package. `cop` may import `cannae_kernel` and its
own web and parsing dependencies, and nothing else outside the standard library.

`harness_c2` and `emulators` do not exist yet, so those checks pass trivially today.
They exist so the rule is enforced from the first commit that adds one, rather than
written down and discovered broken later.

The `cop` checks that read source run everywhere. The check that imports `cop` needs the
`cop` extra; it is skipped when that extra is not installed, and the CI job that installs
it sets COP_EXTRA_REQUIRED=1 so that a skip there is a failure.
"""

import ast
import importlib.util
import json
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import lc

REPO_ROOT = Path(__file__).resolve().parents[1]
COP_DIR = REPO_ROOT / "cop"

FORBIDDEN = ("harness_c2", "emulators", "cop")
COP_FORBIDDEN = ("lc", "harness_c2", "emulators", "aureon", "atreides")
COP_ALLOWED_THIRD_PARTY = frozenset(
    {"cannae_kernel", "flask", "werkzeug", "httpx", "yaml", "pydantic"}
)
COP_RUNTIME_DEPENDENCIES = ("flask", "httpx", "yaml", "pydantic", "cannae_kernel")


def _is_forbidden(module_name: str) -> bool:
    root = module_name.split(".", 1)[0]
    return root in FORBIDDEN


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


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
        offenders += [
            f"{path.name}:{line} {name}"
            for line, name in _absolute_imports(path)
            if _is_forbidden(name)
        ]
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


def test_cop_source_imports_only_what_it_may() -> None:
    # Reads source, so it runs even where the cop extra is not installed.
    assert COP_DIR.is_dir()
    forbidden, unexpected = [], []
    for path in sorted(COP_DIR.rglob("*.py")):
        for line, name in _absolute_imports(path):
            root = name.split(".", 1)[0]
            where = f"{path.relative_to(REPO_ROOT)}:{line} {name}"
            if root in COP_FORBIDDEN:
                forbidden.append(where)
            elif (
                root != "cop"
                and root not in sys.stdlib_module_names
                and root not in COP_ALLOWED_THIRD_PARTY
            ):
                unexpected.append(where)
    # Relative imports are not used, so the absolute scan above sees every import.
    relative = [
        str(path.relative_to(REPO_ROOT))
        for path in COP_DIR.rglob("*.py")
        if any(
            isinstance(node, ast.ImportFrom) and node.level > 0
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert forbidden == []
    assert unexpected == []
    assert relative == []


def test_importing_cop_loads_no_lc_or_forbidden_package() -> None:
    missing = [m for m in COP_RUNTIME_DEPENDENCIES if importlib.util.find_spec(m) is None]
    if missing:
        if os.environ.get("COP_EXTRA_REQUIRED") == "1":
            pytest.fail(f"cop extra required but not installed: {missing}")
        pytest.skip(f"cop extra not installed ({', '.join(missing)}); runs in the cop CI job")
    probe = (
        "import importlib, json, pkgutil, sys\n"
        "import cop\n"
        "for m in pkgutil.walk_packages(cop.__path__, 'cop.'):\n"
        "    importlib.import_module(m.name)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    # No operator key or session secret: importing cop.app must not need them.
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEGATE_")}
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, env=env
    )
    loaded = json.loads(result.stdout)
    assert [n for n in loaded if n.split(".", 1)[0] in COP_FORBIDDEN] == []
    assert "cannae_kernel" in loaded
