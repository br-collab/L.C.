"""Schema for ``cop/program.yaml``, the manually maintained program state.

The repository is public (Research Charter §18.5), so the file holds status only:
identifiers, statuses, evidence identifiers, next actions with owners, and decision
identifiers with one-line titles. The schema enforces short single-line text to keep it
that way.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from cop.observation import ProgramFileError

WAVE_NUMBERS = tuple(range(9))

OneLine = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\n]*$")
]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._#@/-]{0,59}$")]


class WorkStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"


class Owner(StrEnum):
    BILL = "Bill"
    CLAUDE_CODE = "Claude Code"
    COWORK = "Cowork"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NextAction(_Strict):
    action: OneLine
    owner: Owner


class WorkPackage(_Strict):
    id: Identifier
    name: OneLine
    status: WorkStatus
    evidence: tuple[Identifier, ...] = ()


class WorkItem(_Strict):
    id: Identifier
    name: OneLine
    status: WorkStatus
    evidence: tuple[Identifier, ...] = ()
    next_action: NextAction | None = None
    packages: tuple[WorkPackage, ...] = ()

    @model_validator(mode="after")
    def _open_work_has_next_action(self) -> WorkItem:
        if self.status is not WorkStatus.DONE and self.next_action is None:
            raise ValueError(f"{self.id} is {self.status} but has no next action")
        return self


class Wave(WorkItem):
    number: int


class Decision(_Strict):
    id: Identifier
    title: OneLine
    owner: Owner
    opened: date


class Program(_Strict):
    schema_version: int
    as_of: date
    waves: tuple[Wave, ...]
    other_work: tuple[WorkItem, ...] = ()
    decisions: tuple[Decision, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Program:
        if self.schema_version != 1:
            raise ValueError(f"unsupported schema_version {self.schema_version}")
        numbers = tuple(wave.number for wave in self.waves)
        if numbers != WAVE_NUMBERS:
            raise ValueError("waves must be listed once each, in order, numbered 0 to 8")
        ids = [item.id for item in (*self.waves, *self.other_work)]
        ids += [pkg.id for item in (*self.waves, *self.other_work) for pkg in item.packages]
        ids += [decision.id for decision in self.decisions]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate identifiers: {', '.join(duplicates)}")
        return self


def load_program(path: Path) -> Program:
    """Read and validate the program file, or raise ``ProgramFileError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProgramFileError(f"Cannot read {path.name} ({type(exc).__name__})") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ProgramFileError(f"{path.name} is not valid YAML") from None
    try:
        return Program.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "(top level)"
        raise ProgramFileError(
            f"{path.name} failed validation ({exc.error_count()} problems; first at {where})"
        ) from None
