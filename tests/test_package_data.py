"""Every file L.C. reads at runtime is declared as package data.

``lc.occ_rules`` reads the OCC table through ``Path(__file__)``. An editable install
reads it in place, so nothing fails until L.C. is installed from a wheel. This test
fails if the file is not matched by the package-data declaration in ``pyproject.toml``.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

import lc
from lc.occ_rules import DEFAULT_TABLE_PATH

REPO = Path(__file__).resolve().parents[1]


def test_the_occ_table_is_declared_as_package_data() -> None:
    with (REPO / "pyproject.toml").open("rb") as handle:
        patterns = tomllib.load(handle)["tool"]["setuptools"]["package-data"]["lc"]
    name = DEFAULT_TABLE_PATH.relative_to(Path(lc.__file__).resolve().parent).as_posix()
    assert DEFAULT_TABLE_PATH.is_file()
    assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), name
