"""Read-only analysis for EU5 ``profiling_roots.csv`` files."""

from __future__ import annotations

import csv
import os
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Iterable, Mapping, Sequence


class AnalysisError(RuntimeError):
    """Raised when profiling input cannot be analyzed."""


@dataclass(frozen=True)
class Metric:
    key: str
    csv_name: str
    label: str


METRICS: Mapping[str, Metric] = {
    "bottleneck-time": Metric(
        "bottleneck-time",
        "Bottleneck Time",
        "Bottleneck Time",
    ),
    "call-count": Metric("call-count", "Call Count", "Call Count"),
    "max-time": Metric("max-time", "Max Time", "Max Time"),
    "min-time": Metric("min-time", "Min Time", "Min Time"),
    "self-time": Metric("self-time", "Self Time", "Self Time"),
    "total-time": Metric("total-time", "Total Time", "Total Time"),
    "avg-inclusive": Metric(
        "avg-inclusive",
        "Average Time (Inclusive)",
        "Average Time (Inclusive)",
    ),
    "avg-exclusive": Metric(
        "avg-exclusive",
        "Average Time (Exclusive)",
        "Average Time (Exclusive)",
    ),
}
METRIC_CHOICES = set(METRICS)
NUMERIC_COLUMNS = tuple(metric.csv_name for metric in METRICS.values())
DEFAULT_USER_DATA_SUFFIX = Path("Documents/Paradox Interactive/Europa Universalis V")
DEFAULT_REPORT_SOURCE_CONTEXT_LINES = 2
FILE_LOCATION_RE = re.compile(
    r"^(?P<kind>.+?)\s+@\s+(?P<source_path>.*?):(?P<line>\d+)$"
)


@dataclass(frozen=True)
class ParsedLocation:
    raw: str
    kind: str
    source_path: str
    line: int
    parsed: bool


@dataclass(frozen=True)
class SourceRoot:
    path: Path
    kind: str
    label: str


@dataclass(frozen=True)
class SourceContextLine:
    number: int
    text: str
    target: bool = False


@dataclass(frozen=True)
class SourceResolution:
    status: str
    ownership: str
    root: SourceRoot | None = None
    resolved_path: Path | None = None
    line_count: int | None = None
    context: tuple[SourceContextLine, ...] = ()

    @property
    def status_label(self) -> str:
        return self.status.replace("_", " ")


@dataclass(frozen=True)
class ProfilingRow:
    index: int
    file_location: str
    parsed_location: ParsedLocation
    values: Mapping[str, float]
    source: SourceResolution

    def value(self, metric: Metric | str) -> float:
        metric_obj = METRICS[metric] if isinstance(metric, str) else metric
        return self.values.get(metric_obj.csv_name, 0.0)


@dataclass(frozen=True)
class FileAggregate:
    source_path: str
    kind_counts: Mapping[str, int]
    row_count: int
    values: Mapping[str, float]
    source: SourceResolution

    def value(self, metric: Metric | str) -> float:
        metric_obj = METRICS[metric] if isinstance(metric, str) else metric
        return self.values.get(metric_obj.csv_name, 0.0)


@dataclass(frozen=True)
class AnalysisResult:
    csv_path: Path
    row_count: int
    primary_metric: Metric
    top: int
    source_roots: tuple[SourceRoot, ...]
    rows: tuple[ProfilingRow, ...]
    top_rows: tuple[ProfilingRow, ...]
    top_files: Mapping[str, tuple[FileAggregate, ...]]
    mod_hotspots: tuple[FileAggregate, ...]
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def analyze_profiling_roots(
    *,
    csv_path: Path | None = None,
    user_data_root: Path | None = None,
    load_order_path: Path | None = None,
    mod_roots: Sequence[Path] = (),
    vanilla_root: Path | None = None,
    metric: str = "total-time",
    top: int = 20,
) -> AnalysisResult:
    """Analyze a profiling roots CSV and return report-ready data."""

    if top < 1:
        raise AnalysisError("--top must be at least 1")
    if metric not in METRICS:
        raise AnalysisError(f"unknown metric: {metric}")

    resolved_user_data_root = _resolve_user_data_root(user_data_root)
    resolved_csv = _resolve_csv_path(csv_path, resolved_user_data_root)
    if not resolved_csv.is_file():
        raise AnalysisError(f"profiling CSV not found: {resolved_csv}")

    source_roots = discover_source_roots(
        user_data_root=resolved_user_data_root,
        load_order_path=load_order_path,
        mod_roots=mod_roots,
        vanilla_root=vanilla_root,
    )
    rows = parse_profiling_csv(resolved_csv, source_roots=source_roots)
    primary_metric = METRICS[metric]
    top_rows = tuple(sorted(rows, key=lambda row: row.value(primary_metric), reverse=True)[:top])
    top_files = aggregate_top_files(rows, top=top)
    mod_hotspots = tuple(
        aggregate
        for aggregate in top_files["total-time"]
        if aggregate.source.ownership == "mod"
    )
    if len(mod_hotspots) < top:
        all_files = aggregate_files(rows)
        more_mod_hotspots = sorted(
            (
                aggregate
                for aggregate in all_files
                if aggregate.source.ownership == "mod"
                and aggregate not in mod_hotspots
            ),
            key=lambda aggregate: aggregate.value("total-time"),
            reverse=True,
        )
        mod_hotspots = tuple((*mod_hotspots, *more_mod_hotspots)[:top])

    return AnalysisResult(
        csv_path=resolved_csv,
        row_count=len(rows),
        primary_metric=primary_metric,
        top=top,
        source_roots=source_roots,
        rows=tuple(rows),
        top_rows=top_rows,
        top_files=top_files,
        mod_hotspots=mod_hotspots,
    )


def parse_profiling_csv(
    csv_path: Path,
    *,
    source_roots: Sequence[SourceRoot] = (),
) -> list[ProfilingRow]:
    """Parse profiling rows and attach source-resolution metadata."""

    resolver = SourceResolver(source_roots)
    with csv_path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(handle, dialect=dialect)
        _validate_columns(reader.fieldnames)
        rows: list[ProfilingRow] = []
        for index, raw_row in enumerate(reader, start=1):
            parsed_location = parse_file_location(raw_row.get("File Location", ""))
            values = {
                column: parse_number(raw_row.get(column, "0"))
                for column in NUMERIC_COLUMNS
            }
            source = resolver.resolve(parsed_location)
            rows.append(
                ProfilingRow(
                    index=index,
                    file_location=raw_row.get("File Location", ""),
                    parsed_location=parsed_location,
                    values=values,
                    source=source,
                )
            )
    return rows


class SourceResolver:
    """Resolve source references with per-run file and location caches."""

    def __init__(
        self,
        source_roots: Sequence[SourceRoot],
        *,
        context_lines: int = DEFAULT_REPORT_SOURCE_CONTEXT_LINES,
    ) -> None:
        self._source_roots = tuple(source_roots)
        self._context_lines = context_lines
        self._roots_cache_key = "|".join(
            f"{root.kind}:{root.path}" for root in self._source_roots
        )
        self._resolution_cache: dict[tuple[str, int, str], SourceResolution] = {}
        self._file_cache: dict[Path, tuple[str, tuple[str, ...] | None]] = {}

    def resolve(self, location: ParsedLocation) -> SourceResolution:
        cache_key = (
            location.source_path,
            location.line,
            self._roots_cache_key,
        )
        cached = self._resolution_cache.get(cache_key)
        if cached is not None:
            return cached

        resolution = self._resolve_uncached(location)
        self._resolution_cache[cache_key] = resolution
        return resolution

    def _resolve_uncached(self, location: ParsedLocation) -> SourceResolution:
        if not location.parsed:
            return SourceResolution(status="unparsed", ownership="unknown")
        if location.source_path == "<unknown>" or location.line < 1:
            return SourceResolution(status="unknown", ownership="unknown")

        for root in self._source_roots:
            for candidate in _source_candidates(root.path, root.kind, location.source_path):
                status, lines = self._read_source_lines(candidate)
                if status == "missing" or lines is None:
                    continue
                ownership = "mod" if root.kind == "mod" else "vanilla"
                if location.line > len(lines):
                    return SourceResolution(
                        status="line_out_of_range",
                        ownership=ownership,
                        root=root,
                        resolved_path=candidate,
                        line_count=len(lines),
                    )
                return SourceResolution(
                    status="resolved",
                    ownership=ownership,
                    root=root,
                    resolved_path=candidate,
                    line_count=len(lines),
                    context=_context_for_line(lines, location.line, self._context_lines),
                )

        return SourceResolution(status="missing", ownership="unknown")

    def _read_source_lines(self, path: Path) -> tuple[str, tuple[str, ...] | None]:
        cached = self._file_cache.get(path)
        if cached is not None:
            return cached
        if not path.is_file():
            result: tuple[str, tuple[str, ...] | None] = ("missing", None)
        else:
            lines = tuple(
                path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            )
            result = ("found", lines)
        self._file_cache[path] = result
        return result


def parse_file_location(raw: str) -> ParsedLocation:
    match = FILE_LOCATION_RE.match(raw.strip())
    if match is None:
        return ParsedLocation(raw=raw, kind="", source_path="", line=0, parsed=False)
    return ParsedLocation(
        raw=raw,
        kind=match.group("kind").strip(),
        source_path=match.group("source_path").strip(),
        line=int(match.group("line")),
        parsed=True,
    )


def parse_number(raw: object) -> float:
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text:
        return 0.0
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def discover_source_roots(
    *,
    user_data_root: Path | None = None,
    load_order_path: Path | None = None,
    mod_roots: Sequence[Path] = (),
    vanilla_root: Path | None = None,
) -> tuple[SourceRoot, ...]:
    roots: list[SourceRoot] = []

    for raw_root in mod_roots:
        root = normalize_path(raw_root)
        roots.append(SourceRoot(root, "mod", f"mod:{root.name}"))

    if vanilla_root is not None:
        root = normalize_path(vanilla_root)
        roots.append(SourceRoot(root, "vanilla", f"vanilla:{root.name}"))

    if load_order_path is not None:
        roots.extend(_source_roots_from_load_order(normalize_path(load_order_path)))

    if user_data_root is not None:
        roots.extend(_source_roots_from_user_data_root(normalize_path(user_data_root)))

    return _dedupe_source_roots(roots)


def resolve_source(
    location: ParsedLocation,
    source_roots: Sequence[SourceRoot],
    *,
    context_lines: int = DEFAULT_REPORT_SOURCE_CONTEXT_LINES,
) -> SourceResolution:
    return SourceResolver(source_roots, context_lines=context_lines).resolve(location)


def aggregate_files(rows: Sequence[ProfilingRow]) -> tuple[FileAggregate, ...]:
    aggregates: dict[str, dict[str, object]] = {}
    source_by_path: dict[str, SourceResolution] = {}

    for row in rows:
        source_path = row.parsed_location.source_path or "<unparsed>"
        data = aggregates.setdefault(
            source_path,
            {
                "kind_counts": defaultdict(int),
                "row_count": 0,
                "values": defaultdict(float),
            },
        )
        data["row_count"] = int(data["row_count"]) + 1
        kind_counts = data["kind_counts"]
        assert isinstance(kind_counts, defaultdict)
        kind_counts[row.parsed_location.kind or "<unparsed>"] += 1
        values = data["values"]
        assert isinstance(values, defaultdict)
        for column in NUMERIC_COLUMNS:
            values[column] += row.values.get(column, 0.0)
        source_by_path.setdefault(source_path, row.source)

    return tuple(
        FileAggregate(
            source_path=source_path,
            kind_counts=dict(data["kind_counts"]),
            row_count=int(data["row_count"]),
            values=dict(data["values"]),
            source=source_by_path[source_path],
        )
        for source_path, data in aggregates.items()
    )


def aggregate_top_files(
    rows: Sequence[ProfilingRow],
    *,
    top: int,
) -> Mapping[str, tuple[FileAggregate, ...]]:
    aggregates = aggregate_files(rows)
    return {
        metric_key: tuple(
            sorted(
                aggregates,
                key=lambda aggregate, selected=metric_key: aggregate.value(selected),
                reverse=True,
            )[:top]
        )
        for metric_key in (
            "total-time",
            "self-time",
            "bottleneck-time",
            "call-count",
        )
    }


def render_markdown_report(result: AnalysisResult) -> str:
    lines: list[str] = []
    generated = result.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines.extend(
        [
            "# EU5 Profiling Roots Report",
            "",
            f"- Generated: {generated}",
            f"- CSV: `{result.csv_path}`",
            f"- Rows: {_format_number(result.row_count)}",
            f"- Primary metric: {result.primary_metric.label}",
            f"- Source roots: {_format_number(len(result.source_roots))}",
            "",
        ]
    )

    if result.source_roots:
        lines.extend(["## Source Roots", ""])
        for root in result.source_roots:
            lines.append(f"- `{root.kind}` `{root.path}`")
        lines.append("")

    lines.extend(
        [
            f"## Top Rows By {result.primary_metric.label}",
            "",
            "| Rank | Value | Calls | Total | Self | Source | Status |",
            "| ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for rank, row in enumerate(result.top_rows, start=1):
        lines.append(
            "| {rank} | {value} | {calls} | {total} | {self_time} | {source} | {status} |".format(
                rank=rank,
                value=_format_float(row.value(result.primary_metric)),
                calls=_format_float(row.value("call-count")),
                total=_format_float(row.value("total-time")),
                self_time=_format_float(row.value("self-time")),
                source=_escape_table(row.file_location),
                status=_escape_table(_source_status(row.source)),
            )
        )
    lines.append("")

    for metric_key, title in (
        ("total-time", "Top Files By Total Time"),
        ("self-time", "Top Files By Self Time"),
        ("bottleneck-time", "Top Files By Bottleneck Time"),
        ("call-count", "Top Files By Call Count"),
    ):
        lines.extend(_render_file_table(title, result.top_files[metric_key], metric_key))

    lines.extend(["## Mod-Owned Hotspots", ""])
    if not result.mod_hotspots:
        lines.append("No mod-owned source files were resolved from the configured source roots.")
        lines.append("")
    else:
        lines.extend(
            [
                "| Rank | Total | Self | Calls | File | Status |",
                "| ---: | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for rank, aggregate in enumerate(result.mod_hotspots, start=1):
            lines.append(
                "| {rank} | {total} | {self_time} | {calls} | {file} | {status} |".format(
                    rank=rank,
                    total=_format_float(aggregate.value("total-time")),
                    self_time=_format_float(aggregate.value("self-time")),
                    calls=_format_float(aggregate.value("call-count")),
                    file=_escape_table(aggregate.source_path),
                    status=_escape_table(_source_status(aggregate.source)),
                )
            )
        lines.append("")

    lines.extend(["## Source Context", ""])
    for row in result.top_rows:
        lines.extend(_render_row_context(row))

    lines.extend(
        [
            "## Read-Only Next Actions",
            "",
            "- Start with mod-owned files that combine high total time with high call count.",
            "- Treat `line out of range` as source drift: reproduce profiling after syncing the current mod before changing code.",
            "- Treat `<unknown>` engine rows as context for total runtime, not as directly patchable script locations.",
            "- Prefer moving static per-location or per-building calculations into cached variables when repeated script values dominate the report.",
            "",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def normalize_path(path: Path | str) -> Path:
    text = os.fspath(path)
    if _is_windows_drive_path(text):
        pure = PureWindowsPath(text)
        drive = pure.drive.rstrip(":").lower()
        return Path("/mnt") / drive / Path(*pure.parts[1:])
    return Path(text).expanduser()


def default_user_data_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    home = Path.home()
    roots.append(home / DEFAULT_USER_DATA_SUFFIX)

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        roots.append(normalize_path(Path(user_profile) / DEFAULT_USER_DATA_SUFFIX))

    for user_dir in sorted(Path("/mnt/c/Users").glob("*")) if Path("/mnt/c/Users").is_dir() else []:
        roots.append(user_dir / DEFAULT_USER_DATA_SUFFIX)

    return tuple(dict.fromkeys(roots))


def _resolve_user_data_root(user_data_root: Path | None) -> Path | None:
    if user_data_root is not None:
        root = normalize_path(user_data_root)
        if not root.is_dir():
            raise AnalysisError(f"EU5 user-data root not found: {root}")
        return root

    for candidate in default_user_data_roots():
        if candidate.is_dir():
            return candidate
    return None


def _resolve_csv_path(csv_path: Path | None, user_data_root: Path | None) -> Path:
    if csv_path is not None:
        return normalize_path(csv_path)
    if user_data_root is None:
        searched = ", ".join(str(root) for root in default_user_data_roots())
        raise AnalysisError(
            "could not auto-detect EU5 user-data root; pass --csv or --user-data-root. "
            f"Searched: {searched}"
        )
    return user_data_root / "logs" / "profiling_roots.csv"


def _sniff_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def _validate_columns(fieldnames: Sequence[str] | None) -> None:
    if fieldnames is None:
        raise AnalysisError("profiling CSV has no header")
    missing = ["File Location", *NUMERIC_COLUMNS]
    missing = [column for column in missing if column not in fieldnames]
    if missing:
        raise AnalysisError(f"profiling CSV missing columns: {', '.join(missing)}")


def _source_roots_from_load_order(load_order_path: Path) -> list[SourceRoot]:
    if not load_order_path.is_file():
        raise AnalysisError(f"load-order TOML not found: {load_order_path}")

    with load_order_path.open("rb") as handle:
        raw = tomllib.load(handle)
    roots: list[SourceRoot] = []
    paths = raw.get("paths") or {}
    vanilla_root = paths.get("vanilla_root")
    if isinstance(vanilla_root, str) and vanilla_root:
        roots.append(SourceRoot(normalize_path(vanilla_root), "vanilla", "vanilla:load-order"))

    for mod in raw.get("mods") or []:
        root = mod.get("root")
        if not isinstance(root, str) or not root:
            continue
        mod_root = normalize_path(root)
        if not mod_root.is_absolute():
            mod_root = load_order_path.parent / mod_root
        name = str(mod.get("name") or mod.get("id") or mod_root.name)
        roots.append(SourceRoot(mod_root, "mod", f"mod:{name}"))
    return roots


def _source_roots_from_user_data_root(user_data_root: Path) -> list[SourceRoot]:
    mod_root = user_data_root / "mod"
    if not mod_root.is_dir():
        return []
    roots = []
    for candidate in sorted(mod_root.glob("Prosper or Perish*")):
        if candidate.is_dir():
            roots.append(SourceRoot(candidate, "mod", f"mod:{candidate.name}"))
    return roots


def _dedupe_source_roots(roots: Iterable[SourceRoot]) -> tuple[SourceRoot, ...]:
    deduped: list[SourceRoot] = []
    seen: set[tuple[Path, str]] = set()
    for root in roots:
        key = (root.path, root.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return tuple(deduped)


def _source_candidates(root: Path, kind: str, source_path: str) -> tuple[Path, ...]:
    source = Path(source_path)
    roots: list[Path] = []
    if kind == "vanilla":
        game_root = root / "game" if (root / "game").is_dir() else root
        roots.append(game_root)
    else:
        roots.append(root)

    candidates: list[Path] = []
    for base in roots:
        candidates.append(base / source)
        if not _has_game_phase_prefix(source_path):
            candidates.append(base / "in_game" / source)
            candidates.append(base / "main_menu" / source)
    return tuple(dict.fromkeys(candidates))


def _has_game_phase_prefix(source_path: str) -> bool:
    first = source_path.split("/", 1)[0]
    return first in {"in_game", "main_menu", "launcher"}


def _context_for_line(
    lines: Sequence[str],
    target_line: int,
    context_lines: int,
) -> tuple[SourceContextLine, ...]:
    start = max(1, target_line - context_lines)
    end = min(len(lines), target_line + context_lines)
    return tuple(
        SourceContextLine(
            number=line_no,
            text=lines[line_no - 1],
            target=line_no == target_line,
        )
        for line_no in range(start, end + 1)
    )


def _render_file_table(
    title: str,
    aggregates: Sequence[FileAggregate],
    metric_key: str,
) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Rank | Value | Rows | Calls | Total | Self | File | Status |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for rank, aggregate in enumerate(aggregates, start=1):
        lines.append(
            "| {rank} | {value} | {rows} | {calls} | {total} | {self_time} | {file} | {status} |".format(
                rank=rank,
                value=_format_float(aggregate.value(metric_key)),
                rows=_format_number(aggregate.row_count),
                calls=_format_float(aggregate.value("call-count")),
                total=_format_float(aggregate.value("total-time")),
                self_time=_format_float(aggregate.value("self-time")),
                file=_escape_table(aggregate.source_path),
                status=_escape_table(_source_status(aggregate.source)),
            )
        )
    lines.append("")
    return lines


def _render_row_context(row: ProfilingRow) -> list[str]:
    location = row.parsed_location
    source = row.source
    title = location.raw if location.raw else row.file_location
    lines = [f"### `{title}`", "", f"- Status: {_source_status(source)}"]
    if source.resolved_path is not None:
        lines.append(f"- Resolved path: `{source.resolved_path}`")
    if source.status == "line_out_of_range":
        lines.append(
            f"- The profiler referenced line {location.line}, but the current file has {source.line_count} lines."
        )
    lines.append("")
    if source.context:
        lines.append("```text")
        for context_line in source.context:
            marker = ">" if context_line.target else " "
            lines.append(f"{marker} {context_line.number:>5}: {context_line.text}")
        lines.append("```")
        lines.append("")
    return lines


def _source_status(source: SourceResolution) -> str:
    ownership = source.ownership
    if ownership == "unknown":
        return source.status_label
    return f"{ownership}, {source.status_label}"


def _format_float(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _format_number(value: int) -> str:
    return f"{value:,}"


def _escape_table(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _is_windows_drive_path(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))
