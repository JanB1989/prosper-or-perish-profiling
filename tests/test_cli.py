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
