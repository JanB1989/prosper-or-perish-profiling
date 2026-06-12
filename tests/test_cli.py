from __future__ import annotations

import json
import os
from pathlib import Path

from prosper_or_perish_profiling.cli import main


HEADER = (
    "File Location;Bottleneck Time;Call Count;Max Time;Min Time;Self Time;"
    "Total Time;Average Time (Inclusive);Average Time (Exclusive)\n"
)


def test_cli_help(capsys) -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    captured = capsys.readouterr()
    assert "pp-profile" in captured.out


def test_cli_analyze_writes_report_for_explicit_csv(tmp_path: Path, capsys) -> None:
    csv_path = tmp_path / "profiling_roots.csv"
    output = tmp_path / "report.md"
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;2;0;0\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "profiling-roots",
            "analyze",
            "--csv",
            str(csv_path),
            "--output",
            str(output),
            "--no-history",
        ]
    )

    assert exit_code == 0
    assert output.is_file()
    assert "EU5 Profiling Roots Report" in output.read_text(encoding="utf-8")
    assert "rows=1" in capsys.readouterr().out


def test_cli_analyze_writes_html_visualization(tmp_path: Path, capsys) -> None:
    csv_path = tmp_path / "profiling_roots.csv"
    markdown_output = tmp_path / "report.md"
    html_output = tmp_path / "report.html"
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;2;0;0\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "profiling-roots",
            "analyze",
            "--csv",
            str(csv_path),
            "--output",
            str(markdown_output),
            "--html-output",
            str(html_output),
            "--no-history",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert markdown_output.is_file()
    assert html_output.is_file()
    assert "EU5 Profiling Roots Visualization" in html_output.read_text(encoding="utf-8")
    assert f"html_report={html_output}" in captured.out


def test_cli_analyze_writes_metadata_json(tmp_path: Path, capsys) -> None:
    csv_path = tmp_path / "profiling_roots.csv"
    markdown_output = tmp_path / "report.md"
    json_output = tmp_path / "metadata.json"
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;2;0;0\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "profiling-roots",
            "analyze",
            "--csv",
            str(csv_path),
            "--output",
            str(markdown_output),
            "--json-output",
            str(json_output),
            "--no-history",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert markdown_output.is_file()
    assert json_output.is_file()
    assert '"row_count": 1' in json_output.read_text(encoding="utf-8")
    assert f"json_report={json_output}" in captured.out


def test_cli_analyze_tracks_history_and_dedupes_by_capture_metadata(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    csv_path = tmp_path / "profiling_roots.csv"
    performance_log = tmp_path / "performance_degradation.log"
    output = Path("report.md")
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;20;0;0\n",
        encoding="utf-8",
    )
    performance_log.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"5.0","0.030","0.010","0.050","1444_01_01"\n'
        '"15.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )

    args = [
        "profiling-roots",
        "analyze",
        "--csv",
        str(csv_path),
        "--performance-log",
        str(performance_log),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    assert main(args) == 0

    captured = capsys.readouterr()
    history = tmp_path / "reports" / "profiling_run_history.jsonl"
    history_report = tmp_path / "reports" / "profiling_run_history.md"
    entries = [
        json.loads(line)
        for line in history.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(entries) == 1
    assert entries[0]["run_speed"]["game_days_elapsed"] == 10
    assert entries[0]["run_speed"]["seconds_per_game_day"] == 1
    assert history_report.is_file()
    assert "Seconds/Game Day" in history_report.read_text(encoding="utf-8")
    assert "history=reports/profiling_run_history.jsonl" in captured.out
    assert "history_report=reports/profiling_run_history.md" in captured.out


def test_cli_analyze_no_history_disables_tracking(tmp_path: Path, capsys) -> None:
    csv_path = tmp_path / "profiling_roots.csv"
    output = tmp_path / "report.md"
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;2;0;0\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "profiling-roots",
            "analyze",
            "--csv",
            str(csv_path),
            "--output",
            str(output),
            "--no-history",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert not (tmp_path / "profiling_run_history.jsonl").exists()
    assert "history=" not in captured.out


def test_cli_diff_writes_markdown_diff(tmp_path: Path, capsys) -> None:
    before_csv = tmp_path / "before.csv"
    after_csv = tmp_path / "after.csv"
    before_performance_log = tmp_path / "before_performance_degradation.log"
    after_performance_log = tmp_path / "after_performance_degradation.log"
    output = tmp_path / "diff.md"
    before_csv.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;10;0;0\n",
        encoding="utf-8",
    )
    after_csv.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;6;0;0\n",
        encoding="utf-8",
    )
    before_performance_log.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1444_01_01"\n'
        '"20.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )
    after_performance_log.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1444_01_01"\n'
        '"10.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )

    exit_code = main(
        [
            "profiling-roots",
            "diff",
            "--before-csv",
            str(before_csv),
            "--after-csv",
            str(after_csv),
            "--before-performance-log",
            str(before_performance_log),
            "--after-performance-log",
            str(after_performance_log),
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output.is_file()
    diff_text = output.read_text(encoding="utf-8")
    assert "EU5 Profiling Roots Diff" in diff_text
    assert "Run Speed Delta" in diff_text
    assert "Seconds/Game Day" in diff_text
    assert f"diff_report={output}" in captured.out
    assert "before_rows=1 after_rows=1" in captured.out


def test_cli_latest_freezes_live_logs_and_writes_reports(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    user_data_root = tmp_path / "eu5"
    logs = user_data_root / "logs"
    logs.mkdir(parents=True)
    csv_path = logs / "profiling_roots.csv"
    performance_log = logs / "performance_degradation.log"
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;20;0;0\n",
        encoding="utf-8",
    )
    performance_log.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1_01_01"\n'
        '"5.0","0.030","0.010","0.050","1444_01_01"\n'
        '"15.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )
    csv_mtime = 1_800_000_000
    csv_path.touch()
    performance_log.touch()
    os.utime(csv_path, (csv_mtime, csv_mtime))
    os.utime(performance_log, (csv_mtime, csv_mtime))
    Path("pp-profile.local.toml").write_text(
        "[profiling_roots]\n"
        f'user_data_root = "{user_data_root.as_posix()}"\n'
        'report_prefix = "test_profile"\n'
        "top = 5\n"
        "diff_previous = false\n",
        encoding="utf-8",
    )

    exit_code = main(["profiling-roots", "latest"])

    captured = capsys.readouterr()
    assert exit_code == 0
    capture_dir = next((tmp_path / "reports" / "captures").iterdir())
    assert (capture_dir / "profiling_roots.csv").read_text(encoding="utf-8") == (
        csv_path.read_text(encoding="utf-8")
    )
    assert (capture_dir / "performance_degradation.log").is_file()
    stamp = capture_dir.name
    assert (tmp_path / "reports" / f"test_profile_{stamp}.md").is_file()
    assert (tmp_path / "reports" / f"test_profile_{stamp}.html").is_file()
    assert (tmp_path / "reports" / f"test_profile_{stamp}.metadata.json").is_file()
    assert (tmp_path / "reports" / "profiling_run_history.jsonl").is_file()
    assert f"capture=reports/captures/{stamp}" in captured.out
    assert f"report=reports/test_profile_{stamp}.md" in captured.out
    assert "config=pp-profile.local.toml" in captured.out
    assert "rows=1" in captured.out

    frozen_performance_text = (
        capture_dir / "performance_degradation.log"
    ).read_text(encoding="utf-8")
    performance_log.write_text(
        frozen_performance_text
        + '"120.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )
    assert main(["profiling-roots", "latest"]) == 0
    capsys.readouterr()
    assert (
        capture_dir / "performance_degradation.log"
    ).read_text(encoding="utf-8") == frozen_performance_text


def test_cli_latest_diffs_against_previous_capture(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    user_data_root = tmp_path / "eu5"
    logs = user_data_root / "logs"
    logs.mkdir(parents=True)
    old_capture = tmp_path / "reports" / "captures" / "20250101_010101"
    old_capture.mkdir(parents=True)
    old_capture.joinpath("profiling_roots.csv").write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;30;0;0\n",
        encoding="utf-8",
    )
    old_capture.joinpath("performance_degradation.log").write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1444_01_01"\n'
        '"30.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )
    csv_path = logs / "profiling_roots.csv"
    performance_log = logs / "performance_degradation.log"
    csv_path.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;10;0;0\n",
        encoding="utf-8",
    )
    performance_log.write_text(
        '"Total Time","Average Delta","MinDelta","MaxDelta","Game Data"\n'
        '"0.0","0.020","0.010","0.030","1444_01_01"\n'
        '"10.0","0.030","0.010","0.050","1444_01_11"\n',
        encoding="utf-8",
    )
    csv_mtime = 1_800_000_100
    os.utime(csv_path, (csv_mtime, csv_mtime))
    os.utime(performance_log, (csv_mtime, csv_mtime))

    exit_code = main(
        [
            "profiling-roots",
            "latest",
            "--user-data-root",
            str(user_data_root),
            "--report-prefix",
            "test_profile",
            "--no-history",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    current_capture = sorted((tmp_path / "reports" / "captures").iterdir())[-1]
    diff = (
        tmp_path
        / "reports"
        / f"test_profile_diff_20250101_010101_to_{current_capture.name}.md"
    )
    assert diff.is_file()
    assert "EU5 Profiling Roots Diff" in diff.read_text(encoding="utf-8")
    assert f"diff_report=reports/{diff.name}" in captured.out
    assert "history=" not in captured.out


def test_cli_reports_invalid_csv_path(capsys) -> None:
    exit_code = main(
        [
            "profiling-roots",
            "analyze",
            "--csv",
            "/definitely/missing/profiling_roots.csv",
        ]
    )

    assert exit_code == 2
    assert "profiling CSV not found" in capsys.readouterr().err
