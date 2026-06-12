from __future__ import annotations

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
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert markdown_output.is_file()
    assert json_output.is_file()
    assert '"row_count": 1' in json_output.read_text(encoding="utf-8")
    assert f"json_report={json_output}" in captured.out


def test_cli_diff_writes_markdown_diff(tmp_path: Path, capsys) -> None:
    before_csv = tmp_path / "before.csv"
    after_csv = tmp_path / "after.csv"
    output = tmp_path / "diff.md"
    before_csv.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;10;0;0\n",
        encoding="utf-8",
    )
    after_csv.write_text(
        HEADER + "trigger @ <unknown>:0;0;10;0;0;1;6;0;0\n",
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
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert output.is_file()
    assert "EU5 Profiling Roots Diff" in output.read_text(encoding="utf-8")
    assert f"diff_report={output}" in captured.out
    assert "before_rows=1 after_rows=1" in captured.out


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
