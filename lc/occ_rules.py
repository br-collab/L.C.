"""The versioned OCC source-and-rule table, and its loader (ORDER SC-3, WP-6).

EXPERIMENTAL (charter section 18.6). Nothing here is production evidence.

ONE TABLE FOR EVERY OCC FACT
----------------------------
Every OCC (Options Clearing Corporation) procedure, threshold, convention or
figure a computation in L.C. uses is an item in this table, never a literal in
code. Each item names its value, its unit, and the source it was read from:
the URL, the kind of source, the DTG (date-time group, UTC, ``YYYYMMDDHHMM``)
it was retrieved, the SHA-256 of the bytes retrieved, the file name it was
saved under, and the verbatim text that states the value.

FAILS CLOSED
------------
An item is admitted only if it is complete and its verbatim text states its
value. Where the saved source files are at hand (:func:`verify_sources`), an
item whose file does not hash to its recorded SHA-256 is refused as well.
A refused or absent item is not a figure: whatever needs it is INDETERMINATE.

THE COMMITTED TABLE
-------------------
``lc/occ_rule_table.json`` is the table L.C. reads. On 5 October 2026 it holds
no items: OCC's site refuses scripted retrieval from the build environment, so
no OCC source could be retrieved, hashed and quoted. Items are added once the
source files are saved and verified (ORDER SC-3, reserved item 1). Until then,
every check that needs an OCC figure is INDETERMINATE, by design.

The loader takes the table's path explicitly. The JSON file is not declared as
package data, so a built wheel would not carry it; an editable install, which
is how CI and every local run install L.C., reads it in place.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Final

from cannae_kernel.canonical import canonical_bytes_of, digest_bytes
from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "DEFAULT_TABLE_PATH",
    "OccRuleItem",
    "OccRuleTable",
    "OccSource",
    "RefusedItem",
    "SourceKind",
    "load_rule_table",
    "verify_sources",
]

DEFAULT_TABLE_PATH: Final = Path(__file__).with_name("occ_rule_table.json")


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SourceKind(StrEnum):
    WEB_PAGE = "WEB_PAGE"
    RULES = "RULES"
    BY_LAWS = "BY_LAWS"
    INFORMATION_MEMO = "INFORMATION_MEMO"
    PROCEDURES = "PROCEDURES"


class OccSource(_Record):
    url: str = Field(pattern=r"^https://")
    source_kind: SourceKind
    retrieved_dtg: str = Field(pattern=r"^\d{12}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: The name the retrieved bytes were saved under, for re-verification.
    file_name: str = Field(min_length=1)
    #: The source's own words for the value. Never paraphrased.
    verbatim: str = Field(min_length=1)


class OccRuleItem(_Record):
    item_id: str = Field(pattern=r"^occ\.[a-z0-9_.]+$")
    #: The value as a decimal string, so it is exact and never a binary float.
    value: str = Field(min_length=1)
    #: What the value counts, for example ``USD`` or ``contracts``.
    unit: str = Field(min_length=1)
    source: OccSource

    @property
    def decimal_value(self) -> Decimal:
        return Decimal(self.value)


class RefusedItem(_Record):
    item_id: str
    reason: str


class OccRuleTable(_Record):
    table_version: str = Field(min_length=1)
    #: ``sha256:`` over the canonical form of every admitted item, in table order.
    items_digest: str
    items: tuple[OccRuleItem, ...]
    refused: tuple[RefusedItem, ...] = ()

    def item(self, item_id: str) -> OccRuleItem | None:
        return next((i for i in self.items if i.item_id == item_id), None)

    def why_absent(self, item_id: str) -> str:
        refused = next((r for r in self.refused if r.item_id == item_id), None)
        if refused is not None:
            return f"OCC table item {item_id} was refused: {refused.reason}"
        return f"OCC table {self.table_version} has no item {item_id}"


def _states(item: OccRuleItem) -> bool:
    """Whether the verbatim text states the value, as written or with thousands separators."""
    value = item.decimal_value
    spellings = {item.value, f"{value:,}", f"{value:,.2f}"}
    return any(spelling in item.source.verbatim for spelling in spellings)


def load_rule_table(path: Path) -> OccRuleTable:
    """Load and check the table at ``path``. Incomplete or unsupported items are refused."""
    document = json.loads(path.read_text(encoding="utf-8"))
    admitted: list[OccRuleItem] = []
    refused: list[RefusedItem] = []
    seen: set[str] = set()
    for raw in document.get("items", []):
        item_id = str(raw.get("item_id", "?")) if isinstance(raw, dict) else "?"
        try:
            # Validated as JSON, which is what the table is: an enum arrives as its string.
            item = OccRuleItem.model_validate_json(json.dumps(raw))
            item.decimal_value  # noqa: B018 - the value must be a decimal number
        except (ValidationError, InvalidOperation) as error:
            refused.append(RefusedItem(item_id=item_id, reason=f"incomplete: {error}"[:300]))
            continue
        if item.item_id in seen:
            refused.append(RefusedItem(item_id=item_id, reason="the item appears twice"))
        elif not _states(item):
            refused.append(
                RefusedItem(item_id=item_id, reason="its verbatim text does not state its value")
            )
        else:
            admitted.append(item)
        seen.add(item.item_id)
    return OccRuleTable(
        table_version=str(document["table_version"]),
        items_digest=digest_bytes(canonical_bytes_of([i.model_dump() for i in admitted])),
        items=tuple(admitted),
        refused=tuple(refused),
    )


def verify_sources(table: OccRuleTable, directory: Path) -> OccRuleTable:
    """Re-check every admitted item against its saved source file in ``directory``.

    An item whose file is missing or does not hash to its recorded SHA-256 is moved
    to the refused list: mismatched source evidence is not evidence.
    """
    kept: list[OccRuleItem] = []
    refused = list(table.refused)
    for item in table.items:
        path = directory / item.source.file_name
        if not path.is_file():
            refused.append(
                RefusedItem(
                    item_id=item.item_id, reason=f"source file {item.source.file_name} is missing"
                )
            )
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item.source.sha256:
            refused.append(
                RefusedItem(
                    item_id=item.item_id,
                    reason=f"source file {item.source.file_name} does not "
                    f"hash to the recorded SHA-256",
                )
            )
        else:
            kept.append(item)
    return OccRuleTable(
        table_version=table.table_version,
        items_digest=digest_bytes(canonical_bytes_of([i.model_dump() for i in kept])),
        items=tuple(kept),
        refused=tuple(refused),
    )
