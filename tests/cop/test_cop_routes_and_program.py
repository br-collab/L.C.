"""Route inventory (read-only), the program file schema, and packaging rules."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

import pytest
from cop_fakes import MAIN_SHA, Rig, login, section

from cop.app import SECTIONS, create_app
from cop.observation import ProgramFileError
from cop.program import load_program
from cop.settings import PRODUCT_NAME, PROGRAM_FILE, load_settings

ROOT = Path(__file__).resolve().parents[2]
COP = ROOT / "cop"
ALLOWED_POST = {"/login", "/logout"}


def test_every_route_is_get_except_login_and_logout() -> None:
    app = create_app(load_settings({}), start_refresher=False)
    post_routes = set()
    paths = set()
    for rule in app.url_map.iter_rules():
        methods = set(rule.methods or ()) - {"HEAD", "OPTIONS"}
        paths.add(rule.rule)
        if methods != {"GET"}:
            assert methods == {"POST"}, f"{rule.rule} allows {sorted(methods)}"
            post_routes.add(rule.rule)
    assert post_routes == ALLOWED_POST
    # The inventory is exactly what the order specifies.
    assert paths == {
        "/healthz",
        "/login",
        "/logout",
        "/",
        "/panel/<name>",
        "/section/<name>",
        "/static/<path:filename>",
    }


def test_side_rail_routes_every_existing_panel_once_and_keeps_blind_spots_pinned() -> None:
    assigned = [panel for panels in SECTIONS.values() for panel in panels]
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == {
        "waves",
        "repositories",
        "aureon",
        "agents",
        "lifecycles",
        "escalations",
        "breaks",
        "cashleg",
        "blindspots",
        "scheduled",
        "decisions",
        "exceptions",
    }
    assert SECTIONS["blind"] == ("blindspots",)


def test_side_rail_clock_strip_and_single_operator_banner_render_on_every_section() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    for name in SECTIONS:
        html = client.get(f"/section/{name}").get_data(as_text=True)
        assert html.count('aria-current="page"') == 1
        assert "CLOCKS · FROM RECORDS" in html
        assert "Single operator: no separation of duties." in html
        assert "COP clock · last full refresh" in html


def test_sections_without_producers_are_hatched_absent_not_empty_or_zero() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    for name in ("exceptions", "controls", "risk"):
        html = client.get(f"/section/{name}").get_data(as_text=True)
        assert "ABSENT" in html
        assert "tone-absent" in html
        assert "Absent in production" in html


def test_packaged_program_file_is_valid() -> None:
    program = load_program(PROGRAM_FILE)
    assert [w.number for w in program.waves] == list(range(9))
    assert program.waves[0].status == "DONE" and program.waves[2].status == "IN_PROGRESS"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "program.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("status: DONE", "status: FINISHED", 1),
        lambda t: t.replace("  - number: 8", "  - number: 9", 1),
        lambda t: t.replace("id: W2A-2,", "id: W2A-1,", 1),
        lambda t: t.replace("owner: Bill", "owner: Somebody", 1),
        lambda t: t.replace("name: Hygiene", 'name: "Hygiene\\nwith a second line"', 1),
        lambda t: t + "\ndesign_notes: not allowed\n",
        lambda t: "waves: [unclosed",
    ],
)
def test_program_schema_rejects_bad_files(tmp_path: Path, mutate: object) -> None:
    text = PROGRAM_FILE.read_text(encoding="utf-8")
    new_text = mutate(text)  # type: ignore[operator]
    assert new_text != text
    with pytest.raises(ProgramFileError):
        load_program(_write(tmp_path, new_text))


def test_missing_program_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ProgramFileError):
        load_program(tmp_path / "absent.yaml")


def test_invalid_program_file_makes_panels_2_and_6_indeterminate_only(tmp_path: Path) -> None:
    bad = _write(
        tmp_path, PROGRAM_FILE.read_text(encoding="utf-8").replace("status: DONE", "status: ??", 1)
    )
    rig = Rig(program_path=bad)
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    programme_html = client.get("/section/programme").get_data(as_text=True)
    decisions_html = client.get("/section/decisions").get_data(as_text=True)

    waves = section(programme_html, "waves")
    decisions = section(decisions_html, "decisions")
    for panel in (waves, decisions):
        assert "INDETERMINATE" in panel
        assert "InvalidProgramFile" in panel
    assert "<table" not in waves and "<table" not in decisions
    assert "JUM-D-06" not in decisions

    repos = section(programme_html, "repositories")
    assert MAIN_SHA["aureon"][:7] in repos and "INDETERMINATE" not in repos
    aureon = section(programme_html, "aureon")
    assert "Positions" in aureon and ">12<" in aureon
    assert "Nightly" in section(programme_html, "scheduled")


def test_program_validated_at_startup_before_any_refresh(tmp_path: Path) -> None:
    bad = _write(tmp_path, "schema_version: 1\n")
    rig = Rig(program_path=bad)
    rig.app_client()  # create_app validates the file
    assert rig.refresher.snapshot.program.error_class == "InvalidProgramFile"
    assert rig.github.calls == []


def test_valid_program_renders_waves_and_decisions() -> None:
    rig = Rig()
    rig.refresher.refresh_once()
    client = rig.app_client()
    login(client)
    programme_html = client.get("/section/programme").get_data(as_text=True)
    decisions_html = client.get("/section/decisions").get_data(as_text=True)
    waves = section(programme_html, "waves")
    assert "HUMAN_JUDGMENT" in waves and "INDETERMINATE" not in waves
    assert "Project-Atreides#12" in waves
    assert "JUM-D-06" in section(decisions_html, "decisions")


# Packaging -------------------------------------------------------------------------------


def test_product_name_is_written_only_in_settings() -> None:
    offenders = []
    for path in COP.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".html", ".css", ".js", ".yaml"}:
            continue
        if path == COP / "settings.py":
            continue
        # Environment variable names (LEGATE_…) are fixed by the order and are not the name.
        if re.search(rf"\b{PRODUCT_NAME}\b", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_templates_static_files_and_program_are_package_data() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["cop"]
    data_files = [
        p.relative_to(COP).as_posix()
        for p in COP.rglob("*")
        if p.is_file() and p.suffix in {".html", ".css", ".js", ".yaml"}
    ]
    assert data_files
    missing = [f for f in data_files if not any(fnmatch.fnmatch(f, pat) for pat in patterns)]
    assert missing == []
    assert "cop*" in config["tool"]["setuptools"]["packages"]["find"]["include"]
