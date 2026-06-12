from __future__ import annotations

import json
from pathlib import Path

from prosper_or_perish_profiling.profiling_roots import (
    METRICS,
    SourceRoot,
    aggregate_files,
    analyze_profiling_roots,
    likely_duplicate_capacity_evaluations,
    normalize_path,
    parse_file_location,
    parse_number,
    parse_performance_series,
    parse_profiling_csv,
    render_html_report,
    render_markdown_diff,
    render_markdown_report,
    render_metadata_json,
    resolve_source,
    rural_capacity_callsite_rollups,
    script_block_impacts,
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


def test_parse_performance_series_reads_elapsed_and_delta_stats(tmp_path: Path) -> None:
    path = tmp_path / "performance_degradation.log"
    path.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"2.0","0.030","0.010","0.050","not_a_date"\n'
        '"10.0","0.040","0.015","0.100","1444_01_01"\n'
        '"30.0","0.080","0.020","0.200","1444_01_11"\n',
        encoding="utf-8",
    )

    series = parse_performance_series(path)

    assert series is not None
    assert series.sample_count == 4
    assert series.duration_seconds == 30
    assert series.mean_average_delta == 0.0425
    assert series.max_delta == 0.2
    assert series.first_game_date == "1_01_01"
    assert series.last_game_date == "1444_01_11"
    assert series.bootstrap_sample_count == 2
    assert series.gameplay_start_date == "1444_01_01"
    assert series.gameplay_end_date == "1444_01_11"
    assert series.gameplay_elapsed_seconds == 20
    assert series.game_days_elapsed == 10
    assert series.seconds_per_game_day == 2
    assert series.game_days_per_second == 0.5
    assert series.mean_gameplay_average_delta == 0.06


def test_parse_performance_series_marks_speed_unavailable_with_one_gameplay_sample(
    tmp_path: Path,
) -> None:
    path = tmp_path / "performance_degradation.log"
    path.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"10.0","0.040","0.015","0.100","1444_01_01"\n',
        encoding="utf-8",
    )

    series = parse_performance_series(path)

    assert series is not None
    assert series.bootstrap_sample_count == 1
    assert series.gameplay_start_date == "1444_01_01"
    assert series.gameplay_end_date == "1444_01_01"
    assert series.game_days_elapsed == 0
    assert series.gameplay_elapsed_seconds == 0
    assert series.seconds_per_game_day == 0
    assert series.game_days_per_second == 0


def test_parse_performance_series_ignores_trailing_duplicate_final_date_for_speed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "performance_degradation.log"
    path.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"10.0","0.040","0.015","0.100","1444_01_01"\n'
        '"20.0","0.080","0.020","0.200","1444_01_11"\n'
        '"120.0","0.030","0.020","0.050","1444_01_11"\n',
        encoding="utf-8",
    )

    series = parse_performance_series(path)

    assert series is not None
    assert len(series.gameplay_samples) == 3
    assert len(series.gameplay_speed_samples) == 2
    assert series.gameplay_end_date == "1444_01_11"
    assert series.gameplay_elapsed_seconds == 10
    assert series.game_days_elapsed == 10
    assert series.seconds_per_game_day == 1
    assert series.game_days_per_second == 1
    assert series.mean_gameplay_average_delta == 0.06


def test_parse_performance_series_preserves_extra_numeric_columns(tmp_path: Path) -> None:
    path = tmp_path / "performance_degradation.log"
    path.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data","GUI widgets","Memory Usage (MB)"\n'
        '"0.0","0.020","0.010","0.030","1444_01_01","100","5000"\n'
        '"10.0","0.040","0.015","0.100","1444_02_01","140","6000"\n',
        encoding="utf-8",
    )

    series = parse_performance_series(path)

    assert series is not None
    assert series.samples[0].extra_values["GUI widgets"] == 100
    assert series.extra_numeric_summary["GUI widgets"]["last"] == 140
    assert series.extra_numeric_summary["Memory Usage (MB)"]["mean"] == 5500


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


def test_render_html_report_visualizes_mod_impact_and_blocking(tmp_path: Path) -> None:
    user_data_root = tmp_path / "Europa Universalis V"
    logs = user_data_root / "logs"
    mod_root = user_data_root / "mod" / "Prosper or Perish Test"
    source = mod_root / "in_game" / "common" / "script_values" / "pp_building_caps.txt"
    logs.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    write_csv(
        logs / "profiling_roots.csv",
        [
            "named_script_value @ common/script_values/pp_building_caps.txt:2;3;100;0;0;4;12;0;0\n",
            "trigger @ common/scripted_triggers/vanilla.txt:1;1;20;0;0;1;5;0;0\n",
        ],
    )
    (logs / "performance_degradation.log").write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1444_01_01"\n'
        '"10.0","0.040","0.015","0.100","1444_02_01"\n',
        encoding="utf-8",
    )

    result = analyze_profiling_roots(
        user_data_root=user_data_root,
        metric="total-time",
        top=5,
    )
    report = render_html_report(result)

    assert "EU5 Profiling Roots Visualization" in report
    assert "Mod Total Impact" in report
    assert "Mod Blocking Pressure" in report
    assert "Capacity Formulas" in report
    assert "what this blocks or slows" in report
    assert "Bottleneck Time" in report
    assert "Total Time</th><th>% All Total</th><th>% Mod Total" in report
    assert "12 s" in report
    assert "Run Speed From performance_degradation.log" in report
    assert "Seconds/Game Day" in report
    assert "Performance delta over elapsed run seconds" in report
    assert "Estimated Frames/Updates" in report
    assert "Profiler Metric Notes" in report
    assert "common/script_values/pp_building_caps.txt" in report


def test_script_block_impacts_split_shared_files_by_top_level_block(tmp_path: Path) -> None:
    user_data_root = tmp_path / "Europa Universalis V"
    logs = user_data_root / "logs"
    mod_root = user_data_root / "mod" / "Prosper or Perish Test"
    source = mod_root / "in_game" / "common" / "on_action" / "pp_building_culling.txt"
    logs.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    source.write_text(
        "pp_yearly_cull_one_closed_building = {\n"
        "\teffect = {\n"
        "\t\tvalue = 1\n"
        "\t}\n"
        "}\n"
        "\n"
        "pp_ai_victuals_market_on_food_crisis = {\n"
        "\teffect = {\n"
        "\t\tevery_owned_location = {\n"
        "\t\t\tlimit = { is_province_capital = yes }\n"
        "\t\t}\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    write_csv(
        logs / "profiling_roots.csv",
        [
            "effect @ common/on_action/pp_building_culling.txt:2;1;10;0;0;2;5;0;0\n",
            "effect @ common/on_action/pp_building_culling.txt:9;2;20;0;0;4;15;0;0\n",
        ],
    )

    result = analyze_profiling_roots(user_data_root=user_data_root, top=5)
    impacts = script_block_impacts(result.rows)
    markdown = render_markdown_report(result)
    html = render_html_report(result)
    payload = json.loads(render_metadata_json(result))

    assert [(impact.block, impact.value("total-time")) for impact in impacts] == [
        ("pp_ai_victuals_market_on_food_crisis", 15),
        ("pp_yearly_cull_one_closed_building", 5),
    ]
    assert "Top Mod Script Blocks" in markdown
    assert "pp_ai_victuals_market_on_food_crisis" in html
    assert payload["script_block_impacts"][0]["block"] == "pp_ai_victuals_market_on_food_crisis"


def test_rural_capacity_sections_identify_callsites_and_duplicate_gates(tmp_path: Path) -> None:
    user_data_root = tmp_path / "Europa Universalis V"
    logs = user_data_root / "logs"
    mod_root = user_data_root / "mod" / "Prosper or Perish Test"
    capacity_source = mod_root / "in_game" / "common" / "script_values" / "pp_farming_capacity.txt"
    fruit_source = mod_root / "in_game" / "common" / "building_types" / "zz_pp_fruit_orchard.txt"
    logs.mkdir(parents=True)
    capacity_source.parent.mkdir(parents=True)
    fruit_source.parent.mkdir(parents=True)
    capacity_source.write_text("farm_capacity = {\n\tvalue = 1\n}\n", encoding="utf-8")
    fruit_source.write_text(
        "fruit_orchard = {\n"
        "\tmax_levels = farm_capacity\n"
        "\tlocation_potential = {\n"
        "\t\tOR = {\n"
        "\t\t\tpp_orchard_friendly_location_potential = yes\n"
        "\t\t}\n"
        "\t}\n"
        "\tallow = {\n"
        "\t\tfarm_capacity > 0\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    write_csv(
        logs / "profiling_roots.csv",
        [
            "named_script_value @ common/script_values/pp_farming_capacity.txt:1;1;100;0;0;5;20;0;0\n",
            "script_value @ common/building_types/zz_pp_fruit_orchard.txt:2;1;80;0;0;1;8;0;0\n",
            "trigger @ common/building_types/zz_pp_fruit_orchard.txt:8;1;70;0;0;1;7;0;0\n",
            "trigger @ common/building_types/zz_pp_fruit_orchard.txt:9;1;70;0;0;1;6;0;0\n",
            "trigger @ common/building_types/zz_pp_fruit_orchard.txt:3;1;60;0;0;1;5;0;0\n",
        ],
    )

    result = analyze_profiling_roots(user_data_root=user_data_root, top=10)
    callsites = rural_capacity_callsite_rollups(result.rows)
    duplicates = likely_duplicate_capacity_evaluations(result.rows)
    markdown = render_markdown_report(result)
    html = render_html_report(result)

    assert ("farm_capacity", "formula definition") in {
        (callsite.capacity, callsite.surface) for callsite in callsites
    }
    assert ("farm_capacity", "max_levels") in {
        (callsite.capacity, callsite.surface) for callsite in callsites
    }
    assert duplicates
    assert duplicates[0].building == "fruit_orchard"
    assert duplicates[0].capacity == "farm_capacity"
    assert "Rural Capacity Callsite Rollup" in markdown
    assert "Fruit Orchard Focus" in markdown
    assert "Likely Duplicate Rural Capacity Evaluations" in markdown
    assert "Rural Capacity Callsite Rollup" in html


def test_render_markdown_diff_includes_mod_and_rural_capacity_deltas(tmp_path: Path) -> None:
    mod_root = tmp_path / "mod"
    source = mod_root / "in_game" / "common" / "script_values" / "pp_farming_capacity.txt"
    source.parent.mkdir(parents=True)
    source.write_text("farm_capacity = {\n\tvalue = 1\n}\n", encoding="utf-8")
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()
    before_csv = before_dir / "profiling_roots.csv"
    after_csv = after_dir / "profiling_roots.csv"
    write_csv(
        before_csv,
        ["named_script_value @ common/script_values/pp_farming_capacity.txt:1;0;100;0;0;5;20;0;0\n"],
    )
    write_csv(
        after_csv,
        ["named_script_value @ common/script_values/pp_farming_capacity.txt:1;0;100;0;0;3;12;0;0\n"],
    )
    (before_dir / "performance_degradation.log").write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"10.0","0.040","0.015","0.100","1444_01_01"\n'
        '"30.0","0.040","0.015","0.100","1444_01_11"\n',
        encoding="utf-8",
    )
    (after_dir / "performance_degradation.log").write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"8.0","0.030","0.015","0.080","1444_01_01"\n'
        '"18.0","0.030","0.015","0.080","1444_01_11"\n',
        encoding="utf-8",
    )

    before = analyze_profiling_roots(csv_path=before_csv, mod_roots=[mod_root], top=5)
    after = analyze_profiling_roots(csv_path=after_csv, mod_roots=[mod_root], top=5)
    report = render_markdown_diff(before, after)

    assert "EU5 Profiling Roots Diff" in report
    assert "Run Speed Delta" in report
    assert "Seconds/Game Day" in report
    assert "Mod File Delta" in report
    assert "Rural Capacity Callsite Delta" in report
    assert "-8" in report


def test_render_metadata_json_includes_performance_and_rural_sections(tmp_path: Path) -> None:
    user_data_root = tmp_path / "Europa Universalis V"
    logs = user_data_root / "logs"
    mod_root = user_data_root / "mod" / "Prosper or Perish Test"
    source = mod_root / "in_game" / "common" / "script_values" / "pp_farming_capacity.txt"
    logs.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    source.write_text("farm_capacity = {\n\tvalue = 1\n}\n", encoding="utf-8")
    write_csv(
        logs / "profiling_roots.csv",
        ["named_script_value @ common/script_values/pp_farming_capacity.txt:1;0;100;0;0;5;20;0;0\n"],
    )
    (logs / "performance_degradation.log").write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data","GUI widgets"\n'
        '"0.0","0.020","0.010","0.030","1_01_01","100"\n'
        '"10.0","0.040","0.015","0.100","1444_01_01","120"\n'
        '"30.0","0.040","0.015","0.100","1444_01_11","140"\n',
        encoding="utf-8",
    )

    result = analyze_profiling_roots(user_data_root=user_data_root, top=5)
    payload = json.loads(render_metadata_json(result))

    assert payload["row_count"] == 1
    assert payload["performance"]["sample_count"] == 3
    assert payload["performance"]["bootstrap_sample_count"] == 1
    assert payload["performance"]["gameplay_start_date"] == "1444_01_01"
    assert payload["performance"]["game_days_elapsed"] == 10
    assert payload["performance"]["seconds_per_game_day"] == 2
    assert payload["performance"]["estimated_frames_or_updates"] > 0
    assert payload["performance"]["extra_numeric_summary"]["GUI widgets"]["last"] == 140
    assert payload["run_speed"]["mod_profiler_total_per_elapsed_second"] > 0
    assert payload["run_speed"]["mod_profiler_total_per_game_day"] == 2
    assert payload["rural_capacity_callsites"][0]["capacity"] == "farm_capacity"
