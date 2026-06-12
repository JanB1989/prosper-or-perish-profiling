"""Command line interface for Prosper or Perish profiling tools."""

from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from prosper_or_perish_profiling import profiling_roots

DEFAULT_CONFIG_PATHS = (
    Path("pp-profile.local.toml"),
    Path("pp-profile.toml"),
)
DEFAULT_LATEST_REPORT_PREFIX = "rural_capacity_profile"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pp-profile",
        description="Read-only analysis tools for EU5 profiling logs.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    profiling = subcommands.add_parser(
        "profiling-roots",
        help="Analyze logs/profiling_roots.csv.",
        description="Analyze EU5 profiling_roots.csv hotspot data.",
    )
    profiling_subcommands = profiling.add_subparsers(
        dest="profiling_roots_command",
        required=True,
    )

    analyze = profiling_subcommands.add_parser(
        "analyze",
        help="Rank profiler hotspots and write a Markdown report.",
    )
    analyze.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="profiling_roots.csv path. Defaults to logs/profiling_roots.csv under the detected EU5 user-data root.",
    )
    analyze.add_argument(
        "--performance-log",
        type=Path,
        default=None,
        help="performance_degradation.log path. Defaults to a file beside the profiling CSV.",
    )
    analyze.add_argument(
        "--user-data-root",
        type=Path,
        default=None,
        help="EU5 user-data root. Windows and WSL paths are accepted.",
    )
    analyze.add_argument(
        "--load-order",
        type=Path,
        default=None,
        help="Optional constructor-style load-order TOML for source root discovery.",
    )
    analyze.add_argument(
        "--mod-root",
        type=Path,
        action="append",
        default=[],
        help="Mod root to search for profiler source paths. Can be repeated.",
    )
    analyze.add_argument(
        "--vanilla-root",
        type=Path,
        default=None,
        help="EU5 install root containing game/. Windows and WSL paths are accepted.",
    )
    analyze.add_argument(
        "--metric",
        choices=sorted(profiling_roots.METRIC_CHOICES),
        default="total-time",
        help="Primary metric used for top row ranking.",
    )
    analyze.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of rows/files to include per report section.",
    )
    analyze.add_argument(
        "--output",
        type=Path,
        default=Path("reports/profiling_roots.md"),
        help="Markdown report output path. Use '-' to print the report to stdout.",
    )
    analyze.add_argument(
        "--html-output",
        type=Path,
        default=None,
        help="Optional standalone HTML visualization report output path. Use '-' to print the HTML to stdout.",
    )
    analyze.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional machine-readable metadata JSON output path. Use '-' to print the JSON to stdout.",
    )
    analyze.add_argument(
        "--history-output",
        type=Path,
        default=None,
        help="Run-history JSONL path. Defaults to reports/profiling_run_history.jsonl when a file report is written.",
    )
    analyze.add_argument(
        "--history-report-output",
        type=Path,
        default=None,
        help="Run-history Markdown table path. Defaults to profiling_run_history.md beside the history JSONL.",
    )
    analyze.add_argument(
        "--no-history",
        action="store_true",
        help="Do not update profiling run history.",
    )
    analyze.set_defaults(handler=_analyze_profiling_roots)

    latest = profiling_subcommands.add_parser(
        "latest",
        help="Freeze and analyze the latest live EU5 profiling dump.",
        description=(
            "Copy the current live profiling_roots.csv and performance_degradation.log "
            "into reports/captures/<timestamp>/, write timestamped Markdown/HTML/JSON "
            "reports, update history, and diff against the previous capture by default."
        ),
    )
    latest.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config TOML path. Defaults to pp-profile.local.toml, then pp-profile.toml when present.",
    )
    latest.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Live profiling_roots.csv path. Defaults to logs/profiling_roots.csv under the configured or detected EU5 user-data root.",
    )
    latest.add_argument(
        "--performance-log",
        type=Path,
        default=None,
        help="Live performance_degradation.log path. Defaults to a file beside the profiling CSV.",
    )
    latest.add_argument(
        "--user-data-root",
        type=Path,
        default=None,
        help="EU5 user-data root. Overrides config.",
    )
    latest.add_argument(
        "--load-order",
        type=Path,
        default=None,
        help="Constructor load-order TOML for source root discovery. Overrides config.",
    )
    latest.add_argument(
        "--mod-root",
        type=Path,
        action="append",
        default=[],
        help="Additional mod root for source lookup. Can be repeated.",
    )
    latest.add_argument(
        "--vanilla-root",
        type=Path,
        default=None,
        help="EU5 install root containing game/. Overrides config.",
    )
    latest.add_argument(
        "--metric",
        choices=sorted(profiling_roots.METRIC_CHOICES),
        default=None,
        help="Primary metric used for report ranking. Defaults to config or total-time.",
    )
    latest.add_argument(
        "--top",
        type=int,
        default=None,
        help="Number of rows/files per report section. Defaults to config or 100.",
    )
    latest.add_argument(
        "--reports-dir",
        type=Path,
        default=None,
        help="Directory for generated reports. Defaults to config or reports/.",
    )
    latest.add_argument(
        "--captures-dir",
        type=Path,
        default=None,
        help="Directory for frozen live-log captures. Defaults to config or reports/captures/.",
    )
    latest.add_argument(
        "--report-prefix",
        default=None,
        help=f"Report filename prefix. Defaults to config or {DEFAULT_LATEST_REPORT_PREFIX}.",
    )
    latest.add_argument(
        "--diff-previous",
        dest="diff_previous",
        action="store_true",
        default=None,
        help="Diff this capture against the previous frozen capture.",
    )
    latest.add_argument(
        "--no-diff-previous",
        dest="diff_previous",
        action="store_false",
        help="Do not diff against the previous frozen capture.",
    )
    latest.add_argument(
        "--history-output",
        type=Path,
        default=None,
        help="Run-history JSONL path. Defaults to config or reports/profiling_run_history.jsonl.",
    )
    latest.add_argument(
        "--history-report-output",
        type=Path,
        default=None,
        help="Run-history Markdown path. Defaults to the history JSONL path with .md suffix.",
    )
    latest.add_argument(
        "--no-history",
        action="store_true",
        help="Do not update profiling run history.",
    )
    latest.set_defaults(handler=_latest_profiling_roots)

    diff = profiling_subcommands.add_parser(
        "diff",
        help="Compare two profiling_roots.csv captures.",
    )
    diff.add_argument(
        "--before-csv",
        type=Path,
        required=True,
        help="Baseline profiling_roots.csv path.",
    )
    diff.add_argument(
        "--after-csv",
        type=Path,
        required=True,
        help="Comparison profiling_roots.csv path.",
    )
    diff.add_argument(
        "--performance-log",
        type=Path,
        default=None,
        help="Shared performance_degradation.log path fallback for both captures. Defaults to a file beside each profiling CSV.",
    )
    diff.add_argument(
        "--before-performance-log",
        type=Path,
        default=None,
        help="Baseline performance_degradation.log path. Overrides --performance-log for the before capture.",
    )
    diff.add_argument(
        "--after-performance-log",
        type=Path,
        default=None,
        help="Comparison performance_degradation.log path. Overrides --performance-log for the after capture.",
    )
    diff.add_argument(
        "--user-data-root",
        type=Path,
        default=None,
        help="EU5 user-data root. Windows and WSL paths are accepted.",
    )
    diff.add_argument(
        "--load-order",
        type=Path,
        default=None,
        help="Optional constructor-style load-order TOML for source root discovery.",
    )
    diff.add_argument(
        "--mod-root",
        type=Path,
        action="append",
        default=[],
        help="Mod root to search for profiler source paths. Can be repeated.",
    )
    diff.add_argument(
        "--vanilla-root",
        type=Path,
        default=None,
        help="EU5 install root containing game/. Windows and WSL paths are accepted.",
    )
    diff.add_argument(
        "--metric",
        choices=sorted(profiling_roots.METRIC_CHOICES),
        default="total-time",
        help="Primary metric used for top row ranking inside each capture.",
    )
    diff.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of changed files/callsites to include.",
    )
    diff.add_argument(
        "--output",
        type=Path,
        default=Path("reports/profiling_roots_diff.md"),
        help="Markdown diff output path. Use '-' to print the diff to stdout.",
    )
    diff.set_defaults(handler=_diff_profiling_roots)
    return parser


def _analyze_profiling_roots(args: argparse.Namespace) -> int:
    try:
        result = profiling_roots.analyze_profiling_roots(
            csv_path=args.csv,
            performance_log_path=args.performance_log,
            user_data_root=args.user_data_root,
            load_order_path=args.load_order,
            mod_roots=args.mod_root,
            vanilla_root=args.vanilla_root,
            metric=args.metric,
            top=args.top,
        )
    except profiling_roots.AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = profiling_roots.render_markdown_report(result)
    markdown_output_path: Path | None = None
    if str(args.output) == "-":
        print(report)
    else:
        markdown_output_path = profiling_roots.normalize_path(args.output)
        markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_output_path.write_text(report, encoding="utf-8")
        print(f"report={markdown_output_path}")

    html_output_path: Path | None = None
    if args.html_output is not None:
        html_report = profiling_roots.render_html_report(result)
        if str(args.html_output) == "-":
            print(html_report)
        else:
            html_output_path = profiling_roots.normalize_path(args.html_output)
            html_output_path.parent.mkdir(parents=True, exist_ok=True)
            html_output_path.write_text(html_report, encoding="utf-8")
            print(f"html_report={html_output_path}")

    json_output_path: Path | None = None
    if args.json_output is not None:
        json_report = profiling_roots.render_metadata_json(result)
        if str(args.json_output) == "-":
            print(json_report)
        else:
            json_output_path = profiling_roots.normalize_path(args.json_output)
            json_output_path.parent.mkdir(parents=True, exist_ok=True)
            json_output_path.write_text(json_report, encoding="utf-8")
            print(f"json_report={json_output_path}")

    if _should_write_history(args, markdown_output_path, html_output_path, json_output_path):
        history_output = _history_output_path(args)
        history_report_output = _history_report_output_path(args, history_output)
        profiling_roots.update_run_history(
            result,
            history_output,
            history_report_output=history_report_output,
        )
        print(f"history={history_output}")
        print(f"history_report={history_report_output}")

    print(
        "rows={rows} top_metric={metric} source_roots={roots}".format(
            rows=result.row_count,
            metric=result.primary_metric.label,
            roots=len(result.source_roots),
        )
    )
    return 0


def _should_write_history(
    args: argparse.Namespace,
    markdown_output_path: Path | None,
    html_output_path: Path | None,
    json_output_path: Path | None,
) -> bool:
    if args.no_history:
        return False
    return (
        args.history_output is not None
        or markdown_output_path is not None
        or html_output_path is not None
        or json_output_path is not None
    )


def _history_output_path(args: argparse.Namespace) -> Path:
    if args.history_output is not None:
        return profiling_roots.normalize_path(args.history_output)
    return profiling_roots.normalize_path(Path("reports/profiling_run_history.jsonl"))


def _history_report_output_path(args: argparse.Namespace, history_output: Path) -> Path:
    if args.history_report_output is not None:
        return profiling_roots.normalize_path(args.history_report_output)
    return history_output.with_suffix(".md")


def _latest_profiling_roots(args: argparse.Namespace) -> int:
    try:
        config_path, config = _load_config(args.config)
        settings = _profiling_roots_settings(config)

        user_data_root = _path_setting(
            "user_data_root",
            args.user_data_root,
            settings,
            config_path,
        )
        resolved_user_data_root = _latest_user_data_root(user_data_root)
        live_csv = _path_setting("csv", args.csv, settings, config_path)
        if live_csv is None:
            live_csv = resolved_user_data_root / "logs" / "profiling_roots.csv"
        if not live_csv.is_file():
            raise profiling_roots.AnalysisError(f"profiling CSV not found: {live_csv}")

        live_performance_log = _path_setting(
            "performance_log",
            args.performance_log,
            settings,
            config_path,
        )
        if live_performance_log is None:
            live_performance_log = live_csv.parent / "performance_degradation.log"

        reports_dir = _path_setting(
            "reports_dir",
            args.reports_dir,
            settings,
            config_path,
            default=Path("reports"),
        )
        assert reports_dir is not None
        captures_dir = _path_setting(
            "captures_dir",
            args.captures_dir,
            settings,
            config_path,
            default=reports_dir / "captures",
        )
        assert captures_dir is not None
        prefix = str(
            _value_setting(
                "report_prefix",
                args.report_prefix,
                settings,
                DEFAULT_LATEST_REPORT_PREFIX,
            )
        )
        metric = str(_value_setting("metric", args.metric, settings, "total-time"))
        top = int(_value_setting("top", args.top, settings, 100))
        diff_previous = bool(
            _value_setting("diff_previous", args.diff_previous, settings, True)
        )

        load_order = _path_setting("load_order", args.load_order, settings, config_path)
        mod_roots = (
            _path_list_setting("mod_roots", settings, config_path)
            + tuple(args.mod_root or ())
        )
        vanilla_root = _path_setting(
            "vanilla_root",
            args.vanilla_root,
            settings,
            config_path,
        )

        stamp = _capture_stamp(live_csv)
        capture_dir = captures_dir / stamp
        capture_dir.mkdir(parents=True, exist_ok=True)
        frozen_csv = capture_dir / "profiling_roots.csv"
        if not frozen_csv.is_file():
            shutil.copy2(live_csv, frozen_csv)
        frozen_performance_log: Path | None = None
        if live_performance_log.is_file():
            frozen_performance_log = capture_dir / "performance_degradation.log"
            if not frozen_performance_log.is_file():
                shutil.copy2(live_performance_log, frozen_performance_log)

        common_kwargs = {
            "user_data_root": resolved_user_data_root,
            "load_order_path": load_order,
            "mod_roots": mod_roots,
            "vanilla_root": vanilla_root,
            "metric": metric,
            "top": top,
        }
        result = profiling_roots.analyze_profiling_roots(
            csv_path=frozen_csv,
            performance_log_path=frozen_performance_log,
            **common_kwargs,
        )
    except (OSError, profiling_roots.AnalysisError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    markdown_output = reports_dir / f"{prefix}_{stamp}.md"
    html_output = reports_dir / f"{prefix}_{stamp}.html"
    json_output = reports_dir / f"{prefix}_{stamp}.metadata.json"

    _write_text_output(markdown_output, profiling_roots.render_markdown_report(result))
    print(f"capture={capture_dir}")
    print(f"report={markdown_output}")
    _write_text_output(html_output, profiling_roots.render_html_report(result))
    print(f"html_report={html_output}")
    _write_text_output(json_output, profiling_roots.render_metadata_json(result))
    print(f"json_report={json_output}")

    history_output = _path_setting(
        "history_output",
        args.history_output,
        settings,
        config_path,
        default=Path("reports/profiling_run_history.jsonl"),
    )
    assert history_output is not None
    history_report_output = _path_setting(
        "history_report_output",
        args.history_report_output,
        settings,
        config_path,
        default=history_output.with_suffix(".md"),
    )
    assert history_report_output is not None
    if not args.no_history:
        profiling_roots.update_run_history(
            result,
            history_output,
            history_report_output=history_report_output,
        )
        print(f"history={history_output}")
        print(f"history_report={history_report_output}")

    if diff_previous:
        previous_capture = _previous_capture(captures_dir, capture_dir)
        if previous_capture is None:
            print("diff_report=skipped:no_previous_capture")
        else:
            before_performance_log = previous_capture / "performance_degradation.log"
            before = profiling_roots.analyze_profiling_roots(
                csv_path=previous_capture / "profiling_roots.csv",
                performance_log_path=before_performance_log
                if before_performance_log.is_file()
                else None,
                **common_kwargs,
            )
            diff_output = (
                reports_dir
                / f"{prefix}_diff_{previous_capture.name}_to_{capture_dir.name}.md"
            )
            _write_text_output(
                diff_output,
                profiling_roots.render_markdown_diff(before, result),
            )
            print(f"diff_report={diff_output}")

    if config_path is not None:
        print(f"config={config_path}")
    print(
        "rows={rows} top_metric={metric} source_roots={roots}".format(
            rows=result.row_count,
            metric=result.primary_metric.label,
            roots=len(result.source_roots),
        )
    )
    return 0


def _diff_profiling_roots(args: argparse.Namespace) -> int:
    try:
        common_kwargs = {
            "user_data_root": args.user_data_root,
            "load_order_path": args.load_order,
            "mod_roots": args.mod_root,
            "vanilla_root": args.vanilla_root,
            "metric": args.metric,
            "top": args.top,
        }
        before = profiling_roots.analyze_profiling_roots(
            csv_path=args.before_csv,
            performance_log_path=args.before_performance_log or args.performance_log,
            **common_kwargs,
        )
        after = profiling_roots.analyze_profiling_roots(
            csv_path=args.after_csv,
            performance_log_path=args.after_performance_log or args.performance_log,
            **common_kwargs,
        )
    except profiling_roots.AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = profiling_roots.render_markdown_diff(before, after)
    if str(args.output) == "-":
        print(report)
    else:
        output_path = profiling_roots.normalize_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"diff_report={output_path}")

    print(
        "before_rows={before_rows} after_rows={after_rows} top_metric={metric} source_roots={roots}".format(
            before_rows=before.row_count,
            after_rows=after.row_count,
            metric=after.primary_metric.label,
            roots=len(after.source_roots),
        )
    )
    return 0


def _load_config(explicit_path: Path | None) -> tuple[Path | None, Mapping[str, Any]]:
    if explicit_path is not None:
        path = profiling_roots.normalize_path(explicit_path)
        if not path.is_file():
            raise profiling_roots.AnalysisError(f"config TOML not found: {path}")
        return path, _read_toml(path)

    for candidate in DEFAULT_CONFIG_PATHS:
        path = profiling_roots.normalize_path(candidate)
        if path.is_file():
            return path, _read_toml(path)
    return None, {}


def _read_toml(path: Path) -> Mapping[str, Any]:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if not isinstance(raw, dict):
        return {}
    return raw


def _profiling_roots_settings(config: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = config.get("profiling_roots")
    if not isinstance(raw, dict):
        return {}
    latest = raw.get("latest")
    merged = {key: value for key, value in raw.items() if key != "latest"}
    if isinstance(latest, dict):
        merged.update(latest)
    return merged


def _path_setting(
    key: str,
    explicit: Path | None,
    settings: Mapping[str, Any],
    config_path: Path | None,
    *,
    default: Path | None = None,
) -> Path | None:
    raw: object
    if explicit is not None:
        raw = explicit
        base = None
    elif key in settings:
        raw = settings[key]
        base = config_path.parent if config_path is not None else None
    else:
        raw = default
        base = None
    if raw is None:
        return None
    if not isinstance(raw, (str, Path)):
        raise ValueError(f"config key profiling_roots.{key} must be a path string")
    path = profiling_roots.normalize_path(raw)
    if not path.is_absolute() and base is not None:
        path = base / path
    return path


def _path_list_setting(
    key: str,
    settings: Mapping[str, Any],
    config_path: Path | None,
) -> tuple[Path, ...]:
    raw = settings.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"config key profiling_roots.{key} must be a list")
    paths: list[Path] = []
    base = config_path.parent if config_path is not None else None
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"config key profiling_roots.{key} must contain path strings")
        path = profiling_roots.normalize_path(item)
        if not path.is_absolute() and base is not None:
            path = base / path
        paths.append(path)
    return tuple(paths)


def _value_setting(
    key: str,
    explicit: object,
    settings: Mapping[str, Any],
    default: object,
) -> object:
    if explicit is not None:
        return explicit
    return settings.get(key, default)


def _latest_user_data_root(configured: Path | None) -> Path:
    if configured is not None:
        root = profiling_roots.normalize_path(configured)
        if not root.is_dir():
            raise profiling_roots.AnalysisError(f"EU5 user-data root not found: {root}")
        return root

    for candidate in profiling_roots.default_user_data_roots():
        if candidate.is_dir():
            return candidate
    searched = ", ".join(str(root) for root in profiling_roots.default_user_data_roots())
    raise profiling_roots.AnalysisError(
        "could not auto-detect EU5 user-data root; configure profiling_roots.user_data_root. "
        f"Searched: {searched}"
    )


def _capture_stamp(csv_path: Path) -> str:
    timestamp = datetime.fromtimestamp(csv_path.stat().st_mtime)
    return timestamp.strftime("%Y%m%d_%H%M%S")


def _previous_capture(captures_dir: Path, current_capture: Path) -> Path | None:
    if not captures_dir.is_dir():
        return None
    candidates = sorted(
        candidate
        for candidate in captures_dir.iterdir()
        if candidate.is_dir()
        and candidate != current_capture
        and (candidate / "profiling_roots.csv").is_file()
    )
    previous = [candidate for candidate in candidates if candidate.name < current_capture.name]
    if previous:
        return previous[-1]
    return candidates[-1] if candidates else None


def _write_text_output(path: Path, text: str) -> None:
    path = profiling_roots.normalize_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
