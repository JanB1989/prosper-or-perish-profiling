"""Command line interface for Prosper or Perish profiling tools."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from prosper_or_perish_profiling import profiling_roots


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
    analyze.set_defaults(handler=_analyze_profiling_roots)
    return parser


def _analyze_profiling_roots(args: argparse.Namespace) -> int:
    try:
        result = profiling_roots.analyze_profiling_roots(
            csv_path=args.csv,
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
    if str(args.output) == "-":
        print(report)
    else:
        output_path = profiling_roots.normalize_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"report={output_path}")

    print(
        "rows={rows} top_metric={metric} source_roots={roots}".format(
            rows=result.row_count,
            metric=result.primary_metric.label,
            roots=len(result.source_roots),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
