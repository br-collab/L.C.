"""The COP dictionary accounts for every L.C. production module."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LC = ROOT / "lc"
DICTIONARY = ROOT / "cop" / "DATA-DICTIONARY.md"


def test_every_lc_production_module_has_a_dictionary_entry() -> None:
    text = DICTIONARY.read_text(encoding="utf-8")
    modules = sorted(path for path in LC.glob("*.py") if path.name != "__init__.py")
    missing = [path.relative_to(ROOT).as_posix() for path in modules if path.name not in text]
    assert missing == [], f"L.C. production modules absent from the COP dictionary: {missing}"
