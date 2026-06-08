from __future__ import annotations

from pathlib import Path

from prosper_or_perish_profiling.profiling_roots import (
    METRICS,
    SourceRoot,
    aggregate_files,
    analyze_profiling_roots,
    normalize_path,
    parse_file_location,
    parse_number,
    parse_profiling_csv,
    render_markdown_report,
    resolve_source,
)


HEADER = (
    "File Location;Bottleneck Time;Call Count;Max Time;Min Time;Self Time;"
    "Total Time;Average Time (Inclusive);Average Time (Exclusive)\n"
)


def write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(HEADER + "".join(rows), encoding="utf-8")


def test_parse_file_location_splits_kind_path_and_line() -> None:
    parsed = parse_file_location(
        "event (option) @ events/situations/red_turban_rebellions.txt:856"
    )

    assert parsed.parsed is True
    assert parsed.kind == "event (option)"
    assert parsed.source_path == "events/situations/red_turban_rebellions.txt"
    assert parsed.line == 856


def test_parse_file_location_marks_unparsed_values() -> None:
    parsed = parse_file_location("not a profiler location")

    assert parsed.parsed is False
    assert parsed.source_path == ""
    assert parsed.line == 0


def test_parse_number_accepts_decimal_commas_and_bad_values() -> None:
    assert parse_number("1,25") == 1.25
    assert parse_number("3.5") == 3.5
    assert parse_number("") == 0.0
    assert parse_number("nope") == 0.0


def test_parse_profiling_csv_sniffs_semicolon_and_aggregates(tmp_path: Path) -> None:
    csv_path = tmp_path / "profiling_roots.csv"
    write_csv(
        csv_path,
        [
            "trigger @ common/script_values/pp_building_caps.txt:1;0,5;10;0;0;2;5;0;0\n",
            "effect @ common/script_values/pp_building_caps.txt:2;0;3;0;0;1;4;0;0\n",
        ],
    )

    rows = parse_profiling_csv(csv_path)
    aggregate = aggregate_files(rows)[0]

    assert len(rows) == 2
    assert rows[0].value(METRICS["total-time"]) == 5
    assert aggregate.source_path == "common/script_values/pp_building_caps.txt"
    assert aggregate.row_count == 2
    assert aggregate.value("total-time") == 9
    assert aggregate.value("call-count") == 13


def test_normalize_path_converts_windows_drive_to_wsl_mount() -> None:
    assert normalize_path(
        r"C:\Users\Anwender\Documents\Paradox Interactive\Europa Universalis V"
    ) == Path("/mnt/c/Users/Anwender/Documents/Paradox Interactive/Europa Universalis V")


def test_resolve_source_finds_mod_file_with_context(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    source = mod_root / "in_game" / "common" / "script_values" / "pp_building_caps.txt"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    location = parse_file_location(
        "named_script_value @ common/script_values/pp_building_caps.txt:2"
    )

    resolved = resolve_source(location, [SourceRoot(mod_root, "mod", "fixture")])

    assert resolved.status == "resolved"
    assert resolved.ownership == "mod"
    assert resolved.resolved_path == source
    assert [line.number for line in resolved.context] == [1, 2, 3]
    assert [line.text for line in resolved.context if line.target] == ["two"]


def test_resolve_source_reports_line_out_of_range(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    source = mod_root / "main_menu" / "common" / "script_values" / "short.txt"
    source.parent.mkdir(parents=True)
    source.write_text("one\n", encoding="utf-8")
    location = parse_file_location("trigger @ common/script_values/short.txt:9")

    resolved = resolve_source(location, [SourceRoot(mod_root, "mod", "fixture")])

    assert resolved.status == "line_out_of_range"
    assert resolved.ownership == "mod"
    assert resolved.line_count == 1


def test_resolve_source_reports_missing_and_unknown(tmp_path: Path) -> None:
    missing = resolve_source(
        parse_file_location("trigger @ common/script_values/missing.txt:9"),
        [SourceRoot(tmp_path, "mod", "fixture")],
    )
    unknown = resolve_source(parse_file_location("trigger @ <unknown>:0"), [])

    assert missing.status == "missing"
    assert missing.ownership == "unknown"
    assert unknown.status == "unknown"


def test_render_markdown_report_contains_hotspots_and_source_drift(tmp_path: Path) -> None:
    user_data_root = tmp_path / "Europa Universalis V"
    logs = user_data_root / "logs"
    mod_root = user_data_root / "mod" / "Prosper or Perish Test"
    source = mod_root / "in_game" / "common" / "script_values" / "pp_building_caps.txt"
    logs.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    source.write_text("only one line\n", encoding="utf-8")
    write_csv(
        logs / "profiling_roots.csv",
        [
            "named_script_value @ common/script_values/pp_building_caps.txt:2;0;100;0;0;3;12;0;0\n",
            "trigger @ <unknown>:0;0;500;0;0;10;10;0;0\n",
        ],
    )

    result = analyze_profiling_roots(
        user_data_root=user_data_root,
        metric="total-time",
        top=5,
    )
    report = render_markdown_report(result)

    assert result.row_count == 2
    assert "Mod-Owned Hotspots" in report
    assert "common/script_values/pp_building_caps.txt" in report
    assert "line out of range" in report
    assert "Read-Only Next Actions" in report
