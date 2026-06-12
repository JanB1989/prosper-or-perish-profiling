"""Read-only analysis for EU5 ``profiling_roots.csv`` files."""

from __future__ import annotations

import csv
import html
import json
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
RURAL_CAPACITY_KEYS = ("farm_capacity", "fish_capacity", "forest_capacity")
RURAL_CAPACITY_FILES = {
    "farm_capacity": "common/script_values/pp_farming_capacity.txt",
    "fish_capacity": "common/script_values/pp_fishing_capacity.txt",
    "forest_capacity": "common/script_values/pp_forest_capacity.txt",
}
RURAL_CAPACITY_BUILDING_SURFACES = {
    "max_levels",
    "allow block",
    "capacity gate",
}


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
class ScriptBlockRange:
    name: str
    start_line: int
    end_line: int


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
    performance_series: PerformanceSeries | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SystemImpact:
    key: str
    name: str
    blocking_note: str
    row_count: int
    file_count: int
    values: Mapping[str, float]
    top_file: FileAggregate | None = None

    def value(self, metric: Metric | str) -> float:
        metric_obj = METRICS[metric] if isinstance(metric, str) else metric
        return self.values.get(metric_obj.csv_name, 0.0)


@dataclass(frozen=True)
class RuralCapacityCallsite:
    capacity: str
    surface: str
    source_path: str
    row_count: int
    values: Mapping[str, float]

    def value(self, metric: Metric | str) -> float:
        metric_obj = METRICS[metric] if isinstance(metric, str) else metric
        return self.values.get(metric_obj.csv_name, 0.0)


@dataclass(frozen=True)
class BuildingSurfaceImpact:
    building: str
    source_path: str
    surface: str
    row_count: int
    values: Mapping[str, float]

    def value(self, metric: Metric | str) -> float:
        metric_obj = METRICS[metric] if isinstance(metric, str) else metric
        return self.values.get(metric_obj.csv_name, 0.0)


@dataclass(frozen=True)
class ScriptBlockImpact:
    block: str
    source_path: str
    row_count: int
    values: Mapping[str, float]

    def value(self, metric: Metric | str) -> float:
        metric_obj = METRICS[metric] if isinstance(metric, str) else metric
        return self.values.get(metric_obj.csv_name, 0.0)


@dataclass(frozen=True)
class DuplicateCapacityEvaluation:
    building: str
    source_path: str
    capacity: str
    max_levels: RuralCapacityCallsite
    gate: RuralCapacityCallsite

    @property
    def combined_total(self) -> float:
        return self.max_levels.value("total-time") + self.gate.value("total-time")


@dataclass(frozen=True)
class PerformanceSample:
    total_time: float
    average_delta: float
    min_delta: float
    max_delta: float
    game_date: str
    extra_values: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PerformanceSeries:
    path: Path
    samples: tuple[PerformanceSample, ...]

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def duration_seconds(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].total_time - self.samples[0].total_time

    @property
    def first_game_date(self) -> str:
        return self.samples[0].game_date if self.samples else ""

    @property
    def last_game_date(self) -> str:
        return self.samples[-1].game_date if self.samples else ""

    @property
    def mean_average_delta(self) -> float:
        if not self.samples:
            return 0.0
        return sum(sample.average_delta for sample in self.samples) / len(self.samples)

    @property
    def min_delta(self) -> float:
        if not self.samples:
            return 0.0
        return min(sample.min_delta for sample in self.samples)

    @property
    def max_delta(self) -> float:
        if not self.samples:
            return 0.0
        return max(sample.max_delta for sample in self.samples)

    @property
    def estimated_frames_or_ticks(self) -> float:
        return sum(
            _safe_div(_sample_duration(self.samples, index), sample.average_delta)
            for index, sample in enumerate(self.samples)
        )

    @property
    def extra_numeric_summary(self) -> Mapping[str, Mapping[str, float]]:
        columns = sorted(
            {
                column
                for sample in self.samples
                for column, value in sample.extra_values.items()
                if value != 0.0
            }
        )
        summary: dict[str, Mapping[str, float]] = {}
        for column in columns:
            values = [sample.extra_values.get(column, 0.0) for sample in self.samples]
            if not values:
                continue
            summary[column] = {
                "first": values[0],
                "last": values[-1],
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }
        return summary


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
        performance_series=parse_performance_series(
            resolved_csv.parent / "performance_degradation.log"
        ),
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
        self._source_path_cache: dict[
            str,
            tuple[SourceRoot, Path, tuple[str, ...], str] | None,
        ] = {}
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

        matched_source = self._resolve_source_path(location.source_path)
        if matched_source is None:
            return SourceResolution(status="missing", ownership="unknown")

        root, resolved_path, lines, ownership = matched_source
        if location.line > len(lines):
            return SourceResolution(
                status="line_out_of_range",
                ownership=ownership,
                root=root,
                resolved_path=resolved_path,
                line_count=len(lines),
            )
        return SourceResolution(
            status="resolved",
            ownership=ownership,
            root=root,
            resolved_path=resolved_path,
            line_count=len(lines),
            context=_context_for_line(lines, location.line, self._context_lines),
        )

    def _resolve_source_path(
        self,
        source_path: str,
    ) -> tuple[SourceRoot, Path, tuple[str, ...], str] | None:
        if source_path in self._source_path_cache:
            return self._source_path_cache[source_path]

        for root in self._source_roots:
            for candidate in _source_candidates(root.path, root.kind, source_path):
                status, lines = self._read_source_lines(candidate)
                if status == "missing" or lines is None:
                    continue
                ownership = "mod" if root.kind == "mod" else "vanilla"
                result = (root, candidate, lines, ownership)
                self._source_path_cache[source_path] = result
                return result

        self._source_path_cache[source_path] = None
        return None

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


def parse_performance_series(path: Path) -> PerformanceSeries | None:
    if not path.is_file():
        return None

    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle, skipinitialspace=True)
        samples = []
        for raw_row in reader:
            row = {_clean_performance_key(key): value for key, value in raw_row.items()}
            if "Total Time" not in row:
                continue
            extra_values = {
                key: parse_number(value)
                for key, value in row.items()
                if key
                not in {
                    "Total Time",
                    "Average Delta",
                    "MinDelta",
                    "MaxDelta",
                    "Game Data",
                }
            }
            samples.append(
                PerformanceSample(
                    total_time=parse_number(row.get("Total Time")),
                    average_delta=parse_number(row.get("Average Delta")),
                    min_delta=parse_number(row.get("MinDelta")),
                    max_delta=parse_number(row.get("MaxDelta")),
                    game_date=str(row.get("Game Data") or "").strip(),
                    extra_values=extra_values,
                )
            )
    if not samples:
        return None
    return PerformanceSeries(path=path, samples=tuple(samples))


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


def rural_capacity_callsite_rollups(
    rows: Sequence[ProfilingRow],
) -> tuple[RuralCapacityCallsite, ...]:
    """Group rural capacity profiler rows by capacity, caller surface, and file."""

    buckets: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        if row.source.ownership != "mod":
            continue
        capacities = _rural_capacity_references(row)
        if not capacities:
            continue
        source_path = row.parsed_location.source_path or "<unparsed>"
        surface = _rural_capacity_surface(row)
        for capacity in capacities:
            key = (capacity, surface, source_path)
            bucket = buckets.setdefault(
                key,
                {
                    "row_count": 0,
                    "values": _empty_metric_totals(),
                },
            )
            bucket["row_count"] = int(bucket["row_count"]) + 1
            values = bucket["values"]
            assert isinstance(values, dict)
            for column in NUMERIC_COLUMNS:
                values[column] += row.values.get(column, 0.0)

    callsites = []
    for (capacity, surface, source_path), bucket in buckets.items():
        values = bucket["values"]
        assert isinstance(values, dict)
        callsites.append(
            RuralCapacityCallsite(
                capacity=capacity,
                surface=surface,
                source_path=source_path,
                row_count=int(bucket["row_count"]),
                values=dict(values),
            )
        )
    return tuple(
        sorted(callsites, key=lambda callsite: callsite.value("total-time"), reverse=True)
    )


def building_surface_impacts(
    rows: Sequence[ProfilingRow],
) -> tuple[BuildingSurfaceImpact, ...]:
    """Group building definition profiler rows by UI/script surface."""

    buckets: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        source_path = row.parsed_location.source_path or "<unparsed>"
        if row.source.ownership != "mod" or not source_path.startswith("common/building_types/"):
            continue
        building = _building_name_from_source_path(source_path)
        surface = _building_surface(row)
        key = (building, source_path, surface)
        bucket = buckets.setdefault(
            key,
            {
                "row_count": 0,
                "values": _empty_metric_totals(),
            },
        )
        bucket["row_count"] = int(bucket["row_count"]) + 1
        values = bucket["values"]
        assert isinstance(values, dict)
        for column in NUMERIC_COLUMNS:
            values[column] += row.values.get(column, 0.0)

    impacts = []
    for (building, source_path, surface), bucket in buckets.items():
        values = bucket["values"]
        assert isinstance(values, dict)
        impacts.append(
            BuildingSurfaceImpact(
                building=building,
                source_path=source_path,
                surface=surface,
                row_count=int(bucket["row_count"]),
                values=dict(values),
            )
        )
    return tuple(sorted(impacts, key=lambda impact: impact.value("total-time"), reverse=True))


def script_block_impacts(
    rows: Sequence[ProfilingRow],
) -> tuple[ScriptBlockImpact, ...]:
    """Group mod-owned profiler rows by enclosing top-level script block."""

    buckets: dict[tuple[str, str], dict[str, object]] = {}
    block_cache: dict[Path, tuple[ScriptBlockRange, ...]] = {}
    for row in rows:
        if row.source.ownership != "mod" or row.source.resolved_path is None:
            continue
        source_path = row.parsed_location.source_path or "<unparsed>"
        blocks = block_cache.setdefault(
            row.source.resolved_path,
            _top_level_script_blocks(row.source.resolved_path),
        )
        block = _script_block_for_line(blocks, row.parsed_location.line)
        key = (source_path, block)
        bucket = buckets.setdefault(
            key,
            {
                "row_count": 0,
                "values": _empty_metric_totals(),
            },
        )
        bucket["row_count"] = int(bucket["row_count"]) + 1
        values = bucket["values"]
        assert isinstance(values, dict)
        for column in NUMERIC_COLUMNS:
            values[column] += row.values.get(column, 0.0)

    impacts = []
    for (source_path, block), bucket in buckets.items():
        values = bucket["values"]
        assert isinstance(values, dict)
        impacts.append(
            ScriptBlockImpact(
                block=block,
                source_path=source_path,
                row_count=int(bucket["row_count"]),
                values=dict(values),
            )
        )
    return tuple(sorted(impacts, key=lambda impact: impact.value("total-time"), reverse=True))


def likely_duplicate_capacity_evaluations(
    rows: Sequence[ProfilingRow],
) -> tuple[DuplicateCapacityEvaluation, ...]:
    """Find building files that evaluate the same capacity in max_levels and allow."""

    grouped: dict[tuple[str, str], dict[str, list[RuralCapacityCallsite]]] = defaultdict(lambda: defaultdict(list))
    for callsite in rural_capacity_callsite_rollups(rows):
        if not callsite.source_path.startswith("common/building_types/"):
            continue
        if callsite.surface not in RURAL_CAPACITY_BUILDING_SURFACES:
            continue
        grouped[(callsite.source_path, callsite.capacity)][callsite.surface].append(callsite)

    duplicates = []
    for (source_path, capacity), surfaces in grouped.items():
        max_levels = _merge_rural_callsites(surfaces.get("max_levels", ()))
        gate = _merge_rural_callsites(
            (
                *surfaces.get("allow block", ()),
                *surfaces.get("capacity gate", ()),
            )
        )
        if max_levels is None or gate is None:
            continue
        duplicates.append(
            DuplicateCapacityEvaluation(
                building=_building_name_from_source_path(source_path),
                source_path=source_path,
                capacity=capacity,
                max_levels=max_levels,
                gate=gate,
            )
        )
    return tuple(sorted(duplicates, key=lambda duplicate: duplicate.combined_total, reverse=True))


def _merge_rural_callsites(
    callsites: Sequence[RuralCapacityCallsite],
) -> RuralCapacityCallsite | None:
    if not callsites:
        return None
    values = _empty_metric_totals()
    for callsite in callsites:
        for column in NUMERIC_COLUMNS:
            values[column] += callsite.values.get(column, 0.0)
    first = callsites[0]
    surfaces = tuple(dict.fromkeys(callsite.surface for callsite in callsites))
    return RuralCapacityCallsite(
        capacity=first.capacity,
        surface=" + ".join(surfaces),
        source_path=first.source_path,
        row_count=sum(callsite.row_count for callsite in callsites),
        values=values,
    )


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

    lines.extend(_render_script_block_markdown_section(result))

    lines.extend(_render_rural_capacity_markdown_sections(result))

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


def render_html_report(result: AnalysisResult) -> str:
    """Render a standalone visual HTML report for profiling impact."""

    generated = result.generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    totals = _metric_totals(result.rows)
    ownership_totals = _ownership_metric_totals(result.rows)
    mod_totals = ownership_totals.get("mod", _empty_metric_totals())
    system_impacts = _mod_system_impacts(result)
    mod_files = sorted(
        (aggregate for aggregate in aggregate_files(result.rows) if aggregate.source.ownership == "mod"),
        key=lambda aggregate: aggregate.value("total-time"),
        reverse=True,
    )
    mod_rows = sorted(
        (row for row in result.rows if row.source.ownership == "mod"),
        key=lambda row: row.value("total-time"),
        reverse=True,
    )
    stale_mod_files = [
        aggregate
        for aggregate in mod_files
        if aggregate.source.status in {"line_out_of_range", "missing"}
    ]

    sections = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>EU5 Profiling Roots Visualization</title>",
        "<style>",
        _html_css(),
        "</style>",
        "</head>",
        "<body>",
        '<main class="page">',
        '<section class="hero">',
        "<div>",
        "<h1>EU5 Profiling Roots Visualization</h1>",
        "<p>Read-only impact view for profiler hotspots, bottleneck pressure, and mod-owned script cost.</p>",
        "</div>",
        '<dl class="meta">',
        f"<div><dt>Generated</dt><dd>{_h(generated)}</dd></div>",
        f"<div><dt>Rows</dt><dd>{_format_number(result.row_count)}</dd></div>",
        f"<div><dt>CSV</dt><dd>{_h(str(result.csv_path))}</dd></div>",
        "</dl>",
        "</section>",
        _render_html_summary_cards(totals, mod_totals, result.row_count, len(mod_rows)),
        _render_html_performance_section(result.performance_series),
        _render_html_metric_notes_section(),
        _render_html_ownership_section(totals, ownership_totals),
        _render_html_system_section(system_impacts, totals),
        _render_html_script_block_section(result, mod_totals),
        _render_html_rural_capacity_section(result, mod_totals),
        _render_html_file_section(mod_files, totals, mod_totals),
        _render_html_blocking_rows_section(mod_rows, totals, mod_totals),
        _render_html_source_status_section(stale_mod_files, totals, mod_totals),
        "</main>",
        "</body>",
        "</html>",
    ]
    return "\n".join(sections) + "\n"


def render_markdown_diff(before: AnalysisResult, after: AnalysisResult) -> str:
    """Render a compact before/after Markdown diff for two profiler captures."""

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    before_totals = _metric_totals(before.rows)
    after_totals = _metric_totals(after.rows)
    before_mod = _ownership_metric_totals(before.rows).get("mod", _empty_metric_totals())
    after_mod = _ownership_metric_totals(after.rows).get("mod", _empty_metric_totals())
    lines = [
        "# EU5 Profiling Roots Diff",
        "",
        f"- Generated: {generated}",
        f"- Before CSV: `{before.csv_path}`",
        f"- After CSV: `{after.csv_path}`",
        "",
        "## Overall Delta",
        "",
        "| Metric | Before | After | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for metric_key in ("total-time", "self-time", "bottleneck-time", "call-count"):
        metric = METRICS[metric_key]
        before_value = before_totals[metric.csv_name]
        after_value = after_totals[metric.csv_name]
        lines.append(
            f"| All {metric.label} | {_format_float(before_value)} | {_format_float(after_value)} | {_format_signed_float(after_value - before_value)} |"
        )
        before_value = before_mod[metric.csv_name]
        after_value = after_mod[metric.csv_name]
        lines.append(
            f"| Mod {metric.label} | {_format_float(before_value)} | {_format_float(after_value)} | {_format_signed_float(after_value - before_value)} |"
        )
    lines.append("")
    lines.extend(_render_file_delta_table(before, after, title="Mod File Delta", top=max(before.top, after.top)))
    lines.extend(_render_rural_capacity_delta_table(before, after, top=max(before.top, after.top)))
    return "\n".join(lines).rstrip() + "\n"


def render_metadata_json(result: AnalysisResult) -> str:
    """Render machine-readable metadata for a profiling run."""

    totals = _metric_totals(result.rows)
    ownership_totals = _ownership_metric_totals(result.rows)
    mod_totals = ownership_totals.get("mod", _empty_metric_totals())
    rural_callsites = rural_capacity_callsite_rollups(result.rows)
    building_surfaces = building_surface_impacts(result.rows)
    block_impacts = script_block_impacts(result.rows)
    duplicates = likely_duplicate_capacity_evaluations(result.rows)
    payload = {
        "generated_at": result.generated_at.astimezone(UTC).isoformat(),
        "csv": _path_metadata(result.csv_path),
        "row_count": result.row_count,
        "primary_metric": result.primary_metric.key,
        "top": result.top,
        "source_roots": [
            {
                "kind": root.kind,
                "label": root.label,
                "path": str(root.path),
            }
            for root in result.source_roots
        ],
        "totals": _json_metric_totals(totals),
        "ownership_totals": {
            ownership: _json_metric_totals(values)
            for ownership, values in sorted(ownership_totals.items())
        },
        "mod_systems": [
            {
                "key": impact.key,
                "name": impact.name,
                "row_count": impact.row_count,
                "file_count": impact.file_count,
                "top_file": impact.top_file.source_path if impact.top_file else None,
                "metrics": _json_metric_totals(impact.values),
            }
            for impact in _mod_system_impacts(result)
        ],
        "performance": _performance_metadata(result.performance_series),
        "script_block_impacts": [
            {
                "block": impact.block,
                "source_path": impact.source_path,
                "row_count": impact.row_count,
                "metrics": _json_metric_totals(impact.values),
            }
            for impact in block_impacts
        ],
        "rural_capacity_callsites": [
            {
                "capacity": callsite.capacity,
                "surface": callsite.surface,
                "source_path": callsite.source_path,
                "row_count": callsite.row_count,
                "metrics": _json_metric_totals(callsite.values),
            }
            for callsite in rural_callsites
        ],
        "building_surface_impacts": [
            {
                "building": impact.building,
                "surface": impact.surface,
                "source_path": impact.source_path,
                "row_count": impact.row_count,
                "metrics": _json_metric_totals(impact.values),
            }
            for impact in building_surfaces
        ],
        "likely_duplicate_capacity_evaluations": [
            {
                "building": duplicate.building,
                "source_path": duplicate.source_path,
                "capacity": duplicate.capacity,
                "combined_total": duplicate.combined_total,
                "max_levels": {
                    "row_count": duplicate.max_levels.row_count,
                    "metrics": _json_metric_totals(duplicate.max_levels.values),
                },
                "gate": {
                    "surface": duplicate.gate.surface,
                    "row_count": duplicate.gate.row_count,
                    "metrics": _json_metric_totals(duplicate.gate.values),
                },
            }
            for duplicate in duplicates
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _render_script_block_markdown_section(result: AnalysisResult) -> list[str]:
    impacts = script_block_impacts(result.rows)
    lines = [
        "## Top Mod Script Blocks",
        "",
        "Rows are grouped by the enclosing top-level script block. This helps distinguish a hot action, trigger, scripted value, or building block inside a shared file.",
        "",
    ]
    if not impacts:
        lines.extend(["No resolved mod-owned script blocks were found.", ""])
        return lines
    lines.extend(
        [
            "| Rank | Block | Total | Self | Bottleneck | Calls | Rows | Source |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for rank, impact in enumerate(impacts[: result.top * 2], start=1):
        lines.append(
            "| {rank} | {block} | {total} | {self_time} | {bottleneck} | {calls} | {rows} | {source} |".format(
                rank=rank,
                block=_escape_table(impact.block),
                total=_format_float(impact.value("total-time")),
                self_time=_format_float(impact.value("self-time")),
                bottleneck=_format_float(impact.value("bottleneck-time")),
                calls=_format_float(impact.value("call-count")),
                rows=_format_number(impact.row_count),
                source=_escape_table(impact.source_path),
            )
        )
    lines.append("")
    return lines


def _render_rural_capacity_markdown_sections(result: AnalysisResult) -> list[str]:
    lines: list[str] = []
    callsites = rural_capacity_callsite_rollups(result.rows)
    building_surfaces = building_surface_impacts(result.rows)
    duplicates = likely_duplicate_capacity_evaluations(result.rows)

    lines.extend(
        [
            "## Rural Capacity Callsite Rollup",
            "",
            "Rows are grouped by the capacity value they reference and the script surface that invoked it. Formula-definition rows are the capacity scripted-value files themselves; building rows show where `max_levels`, `allow`, or static potentials pull on them.",
            "",
        ]
    )
    if not callsites:
        lines.extend(["No resolved mod-owned rural capacity callsites were found.", ""])
    else:
        lines.extend(
            [
                "| Capacity | Surface | Total | Self | Bottleneck | Calls | Rows | Source |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for callsite in callsites[: result.top * 2]:
            lines.append(
                "| {capacity} | {surface} | {total} | {self_time} | {bottleneck} | {calls} | {rows} | {source} |".format(
                    capacity=_escape_table(callsite.capacity),
                    surface=_escape_table(callsite.surface),
                    total=_format_float(callsite.value("total-time")),
                    self_time=_format_float(callsite.value("self-time")),
                    bottleneck=_format_float(callsite.value("bottleneck-time")),
                    calls=_format_float(callsite.value("call-count")),
                    rows=_format_number(callsite.row_count),
                    source=_escape_table(callsite.source_path),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Building Surface Breakdown",
            "",
            "| Building | Surface | Total | Self | Bottleneck | Calls | Rows | Source |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for impact in building_surfaces[: result.top * 2]:
        lines.append(
            "| {building} | {surface} | {total} | {self_time} | {bottleneck} | {calls} | {rows} | {source} |".format(
                building=_escape_table(impact.building),
                surface=_escape_table(impact.surface),
                total=_format_float(impact.value("total-time")),
                self_time=_format_float(impact.value("self-time")),
                bottleneck=_format_float(impact.value("bottleneck-time")),
                calls=_format_float(impact.value("call-count")),
                rows=_format_number(impact.row_count),
                source=_escape_table(impact.source_path),
            )
        )
    lines.append("")

    lines.extend(_render_fruit_orchard_focus(result, building_surfaces))

    lines.extend(
        [
            "## Likely Duplicate Rural Capacity Evaluations",
            "",
            "These building files reference the same rural capacity in both `max_levels` and an allow/capacity gate. They are candidates for in-game UI verification before any gate removal.",
            "",
        ]
    )
    if not duplicates:
        lines.extend(["No likely duplicate rural capacity evaluations were detected.", ""])
    else:
        lines.extend(
            [
                "| Building | Capacity | Max-Level Total | Gate Total | Combined Total | Max Calls | Gate Calls | Source |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for duplicate in duplicates[: result.top * 2]:
            lines.append(
                "| {building} | {capacity} | {max_total} | {gate_total} | {combined} | {max_calls} | {gate_calls} | {source} |".format(
                    building=_escape_table(duplicate.building),
                    capacity=_escape_table(duplicate.capacity),
                    max_total=_format_float(duplicate.max_levels.value("total-time")),
                    gate_total=_format_float(duplicate.gate.value("total-time")),
                    combined=_format_float(duplicate.combined_total),
                    max_calls=_format_float(duplicate.max_levels.value("call-count")),
                    gate_calls=_format_float(duplicate.gate.value("call-count")),
                    source=_escape_table(duplicate.source_path),
                )
            )
        lines.append("")

    return lines


def _render_fruit_orchard_focus(
    result: AnalysisResult,
    building_surfaces: Sequence[BuildingSurfaceImpact],
) -> list[str]:
    fruit_source = "common/building_types/zz_pp_fruit_orchard.txt"
    fruit_surfaces = [impact for impact in building_surfaces if impact.source_path == fruit_source]
    fruit_rows = sorted(
        (
            row
            for row in result.rows
            if row.source.ownership == "mod"
            and row.parsed_location.source_path == fruit_source
        ),
        key=lambda row: row.value("total-time"),
        reverse=True,
    )
    lines = [
        "## Fruit Orchard Focus",
        "",
    ]
    if not fruit_surfaces:
        lines.extend(["No resolved fruit orchard profiler rows were found.", ""])
        return lines
    total = sum(impact.value("total-time") for impact in fruit_surfaces)
    lines.extend(
        [
            f"- Fruit orchard building-definition total: {_format_float(total)}",
            "",
            "| Surface | Total | Self | Bottleneck | Calls | Rows |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for impact in fruit_surfaces:
        lines.append(
            "| {surface} | {total} | {self_time} | {bottleneck} | {calls} | {rows} |".format(
                surface=_escape_table(impact.surface),
                total=_format_float(impact.value("total-time")),
                self_time=_format_float(impact.value("self-time")),
                bottleneck=_format_float(impact.value("bottleneck-time")),
                calls=_format_float(impact.value("call-count")),
                rows=_format_number(impact.row_count),
            )
        )
    lines.extend(
        [
            "",
            "| Row | Surface | Total | Calls | Source Line |",
            "| --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in fruit_rows[: min(result.top, 12)]:
        lines.append(
            "| {row} | {surface} | {total} | {calls} | {line} |".format(
                row=_escape_table(row.file_location),
                surface=_escape_table(_building_surface(row)),
                total=_format_float(row.value("total-time")),
                calls=_format_float(row.value("call-count")),
                line=_escape_table(_target_source_text(row)),
            )
        )
    lines.append("")
    return lines


def _render_html_rural_capacity_section(
    result: AnalysisResult,
    mod_totals: Mapping[str, float],
) -> str:
    callsites = rural_capacity_callsite_rollups(result.rows)
    building_surfaces = building_surface_impacts(result.rows)
    duplicates = likely_duplicate_capacity_evaluations(result.rows)
    rows = [
        '<section class="panel">',
        "<h2>Rural Capacity Callsite Rollup</h2>",
        "<p>Grouped by capacity value, caller surface, and source file. This separates direct formula cost from building max-level, allow, map-mode, and culling consumers.</p>",
        '<table class="data-table">',
        "<thead><tr><th>Capacity</th><th>Surface</th><th>Total</th><th>% Mod Total</th><th>Self</th><th>Bottleneck</th><th>Calls</th><th>Rows</th><th>Source</th></tr></thead>",
        "<tbody>",
    ]
    for callsite in callsites[: result.top * 2]:
        rows.append(
            "<tr>"
            f"<td><code>{_h(callsite.capacity)}</code></td>"
            f"<td>{_h(callsite.surface)}</td>"
            f"<td>{_format_seconds(callsite.value('total-time'))}</td>"
            f"<td>{_format_percent(_share(callsite.value('total-time'), mod_totals['Total Time']))}</td>"
            f"<td>{_format_seconds(callsite.value('self-time'))}</td>"
            f"<td>{_format_seconds(callsite.value('bottleneck-time'))}</td>"
            f"<td>{_format_float(callsite.value('call-count'))}</td>"
            f"<td>{_format_number(callsite.row_count)}</td>"
            f"<td><code>{_h(callsite.source_path)}</code></td>"
            "</tr>"
        )
    rows.extend(
        [
            "</tbody>",
            "</table>",
            "<h2>Building Surface Breakdown</h2>",
            '<table class="data-table">',
            "<thead><tr><th>Building</th><th>Surface</th><th>Total</th><th>% Mod Total</th><th>Self</th><th>Bottleneck</th><th>Calls</th><th>Rows</th><th>Source</th></tr></thead>",
            "<tbody>",
        ]
    )
    for impact in building_surfaces[: result.top * 2]:
        rows.append(
            "<tr>"
            f"<td><code>{_h(impact.building)}</code></td>"
            f"<td>{_h(impact.surface)}</td>"
            f"<td>{_format_seconds(impact.value('total-time'))}</td>"
            f"<td>{_format_percent(_share(impact.value('total-time'), mod_totals['Total Time']))}</td>"
            f"<td>{_format_seconds(impact.value('self-time'))}</td>"
            f"<td>{_format_seconds(impact.value('bottleneck-time'))}</td>"
            f"<td>{_format_float(impact.value('call-count'))}</td>"
            f"<td>{_format_number(impact.row_count)}</td>"
            f"<td><code>{_h(impact.source_path)}</code></td>"
            "</tr>"
        )
    rows.extend(
        [
            "</tbody>",
            "</table>",
            "<h2>Likely Duplicate Rural Capacity Evaluations</h2>",
            "<p>These are candidates only; removing an allow gate still needs an in-game UI/functionality check.</p>",
            '<table class="data-table">',
            "<thead><tr><th>Building</th><th>Capacity</th><th>Max-Level Total</th><th>Gate Total</th><th>Combined Total</th><th>Max Calls</th><th>Gate Calls</th><th>Source</th></tr></thead>",
            "<tbody>",
        ]
    )
    for duplicate in duplicates[: result.top * 2]:
        rows.append(
            "<tr>"
            f"<td><code>{_h(duplicate.building)}</code></td>"
            f"<td><code>{_h(duplicate.capacity)}</code></td>"
            f"<td>{_format_seconds(duplicate.max_levels.value('total-time'))}</td>"
            f"<td>{_format_seconds(duplicate.gate.value('total-time'))}</td>"
            f"<td>{_format_seconds(duplicate.combined_total)}</td>"
            f"<td>{_format_float(duplicate.max_levels.value('call-count'))}</td>"
            f"<td>{_format_float(duplicate.gate.value('call-count'))}</td>"
            f"<td><code>{_h(duplicate.source_path)}</code></td>"
            "</tr>"
        )
    rows.extend(["</tbody>", "</table>", "</section>"])
    return "\n".join(rows)


def _render_html_summary_cards(
    totals: Mapping[str, float],
    mod_totals: Mapping[str, float],
    row_count: int,
    mod_row_count: int,
) -> str:
    cards = [
        (
            "Mod Total Impact",
            _format_seconds(mod_totals["Total Time"]),
            _format_percent(_share(mod_totals["Total Time"], totals["Total Time"])),
            "Share of total profiler time attributed to resolved mod-owned rows.",
        ),
        (
            "Mod Blocking Pressure",
            _format_seconds(mod_totals["Bottleneck Time"]),
            _format_percent(
                _share(mod_totals["Bottleneck Time"], totals["Bottleneck Time"])
            ),
            "Share of profiler Bottleneck Time, the closest available proxy for what blocks progress.",
        ),
        (
            "Mod Self Cost",
            _format_seconds(mod_totals["Self Time"]),
            _format_percent(_share(mod_totals["Self Time"], totals["Self Time"])),
            "Exclusive time spent inside mod-owned rows.",
        ),
        (
            "Mod Calls",
            _format_float(mod_totals["Call Count"]),
            _format_percent(_share(mod_totals["Call Count"], totals["Call Count"])),
            f"{_format_number(mod_row_count)} of {_format_number(row_count)} profiler rows resolve to mod-owned source.",
        ),
    ]
    html_cards = ['<section class="cards" aria-label="Mod impact summary">']
    for title, value, share, note in cards:
        html_cards.extend(
            [
                '<article class="card">',
                f"<h2>{_h(title)}</h2>",
                f'<p class="metric">{_h(value)}</p>',
                f'<p class="share">{_h(share)}</p>',
                f"<p>{_h(note)}</p>",
                "</article>",
            ]
        )
    html_cards.append("</section>")
    return "\n".join(html_cards)


def _render_html_performance_section(series: PerformanceSeries | None) -> str:
    rows = ['<section class="panel">', "<h2>Run Statistics From performance_degradation.log</h2>"]
    if series is None:
        rows.append(
            "<p>No adjacent <code>performance_degradation.log</code> was found, so this report cannot show run-duration or frame-delta statistics.</p>"
        )
        rows.append("</section>")
        return "\n".join(rows)

    rows.append(
        "<p>The script profiler CSV does not contain total run seconds or tick count. This adjacent log provides elapsed seconds and frame/update delta samples; tick count is not explicit, so the frame/tick value below is an estimate from delta samples.</p>"
    )
    rows.extend(
        [
            '<div class="cards stats-cards">',
            _html_stat_card("Samples", _format_number(series.sample_count), "Rows in performance_degradation.log."),
            _html_stat_card("Elapsed", _format_seconds(series.duration_seconds), f"{_h(series.first_game_date)} to {_h(series.last_game_date)}."),
            _html_stat_card("Mean Avg Delta", _format_delta(series.mean_average_delta), "Average of logged Average Delta values."),
            _html_stat_card("Worst Max Delta", _format_delta(series.max_delta), "Largest logged MaxDelta spike."),
            _html_stat_card("Best Min Delta", _format_delta(series.min_delta), "Smallest logged MinDelta."),
            _html_stat_card("Estimated Frames/Ticks", _format_float(series.estimated_frames_or_ticks), "Derived from sample span divided by Average Delta, not directly exported."),
            "</div>",
            _render_performance_svg(series),
            "</section>",
        ]
    )
    return "\n".join(rows)


def _render_html_metric_notes_section() -> str:
    return "\n".join(
        [
            '<section class="panel notes">',
            "<h2>Profiler Metric Notes</h2>",
            "<p>I could confirm a Paradox-family Script Profiler exists from CK3 1.13 patch notes, but I did not find public Paradox documentation that defines every EU5 <code>profiling_roots.csv</code> column.</p>",
            "<ul>",
            '<li><strong>Total Time</strong>: interpreted as inclusive accumulated profiler time for the row. In standard profiler terminology, total time includes children/callees; Chrome DevTools documents this exact self/total distinction.</li>',
            '<li><strong>Self Time</strong>: interpreted as exclusive accumulated time spent directly in that row, excluding nested work.</li>',
            '<li><strong>Bottleneck Time</strong>: Paradox-specific column name with no public definition found. This report treats it as a blocking-pressure signal and keeps it separate from Total Time for next-action decisions.</li>',
            '<li><strong>Call Count</strong>: number of times the profiled script row was invoked during the captured profiling window.</li>',
            '<li><strong>Total seconds/ticks</strong>: not present in <code>profiling_roots.csv</code>. Elapsed seconds and delta statistics come from <code>performance_degradation.log</code>; ticks are estimated only when shown.</li>',
            "</ul>",
            '<p class="source-links">Sources checked: <a href="https://ck3.paradoxwikis.com/Patch_1.13">CK3 Patch 1.13 Script Profiler note</a>, <a href="https://developer.chrome.com/docs/devtools/performance/reference">Chrome DevTools profiler self/total definitions</a>.</p>',
            "</section>",
        ]
    )


def _render_html_ownership_section(
    totals: Mapping[str, float],
    ownership_totals: Mapping[str, Mapping[str, float]],
) -> str:
    metric_keys = ("total-time", "self-time", "bottleneck-time", "call-count")
    rows = [
        '<section class="panel">',
        "<h2>Whole-Sample Ownership Impact</h2>",
        "<p>Each bar compares resolved mod-owned rows against vanilla and unresolved profiler rows.</p>",
        '<div class="stack-list">',
    ]
    for metric_key in metric_keys:
        metric = METRICS[metric_key]
        total = totals[metric.csv_name]
        rows.append(f'<div class="stack-row"><h3>{_h(metric.label)}</h3>')
        rows.append('<div class="stack-bar">')
        for ownership, css_class in (
            ("mod", "mod"),
            ("vanilla", "vanilla"),
            ("unknown", "unknown"),
        ):
            value = ownership_totals.get(ownership, _empty_metric_totals())[metric.csv_name]
            width = _share(value, total) * 100
            rows.append(
                '<span class="{css_class}" style="width:{width:.3f}%" title="{title}"></span>'.format(
                    css_class=css_class,
                    width=width,
                    title=_h(f"{ownership}: {_format_float(value)}"),
                )
            )
        rows.append("</div>")
        rows.append('<div class="legend compact">')
        for ownership in ("mod", "vanilla", "unknown"):
            value = ownership_totals.get(ownership, _empty_metric_totals())[metric.csv_name]
            rows.append(
                f'<span><b class="swatch {ownership}"></b>{_h(ownership)} '
                f"{_format_percent(_share(value, total))}</span>"
            )
        rows.append("</div></div>")
    rows.extend(["</div>", "</section>"])
    return "\n".join(rows)


def _render_html_system_section(
    system_impacts: Sequence[SystemImpact],
    totals: Mapping[str, float],
) -> str:
    max_total = max((impact.value("total-time") for impact in system_impacts), default=0.0)
    max_bottleneck = max(
        (impact.value("bottleneck-time") for impact in system_impacts),
        default=0.0,
    )
    rows = [
        '<section class="panel">',
        "<h2>Mod Systems: Total Impact And Blocking Pressure</h2>",
        "<p>Total Time shows overall cost. Bottleneck Time shows the profiler's blocking-pressure signal. The notes explain what this blocks or slows in practice.</p>",
        '<div class="system-grid">',
    ]
    for impact in system_impacts:
        top_file = impact.top_file.source_path if impact.top_file else "n/a"
        rows.extend(
            [
                '<article class="system-row">',
                '<div class="system-copy">',
                f"<h3>{_h(impact.name)}</h3>",
                f"<p>{_h(impact.blocking_note)}</p>",
                f'<p class="subtle">Top file: <code>{_h(top_file)}</code></p>',
                "</div>",
                '<div class="bars">',
                _html_bar(
                    "Total",
                    impact.value("total-time"),
                    max_total,
                    _format_percent(_share(impact.value("total-time"), totals["Total Time"])),
                    "total",
                ),
                _html_bar(
                    "Bottleneck",
                    impact.value("bottleneck-time"),
                    max_bottleneck,
                    _format_percent(
                        _share(impact.value("bottleneck-time"), totals["Bottleneck Time"])
                    ),
                    "block",
                ),
                _html_bar(
                    "Calls",
                    impact.value("call-count"),
                    max((i.value("call-count") for i in system_impacts), default=0.0),
                    _format_float(impact.value("call-count")),
                    "calls",
                ),
                "</div>",
                '<dl class="mini-stats">',
                f"<div><dt>Self</dt><dd>{_format_seconds(impact.value('self-time'))}</dd></div>",
                f"<div><dt>Files</dt><dd>{_format_number(impact.file_count)}</dd></div>",
                f"<div><dt>Rows</dt><dd>{_format_number(impact.row_count)}</dd></div>",
                "</dl>",
                "</article>",
            ]
        )
    rows.extend(["</div>", "</section>"])
    return "\n".join(rows)


def _render_html_script_block_section(
    result: AnalysisResult,
    mod_totals: Mapping[str, float],
) -> str:
    impacts = script_block_impacts(result.rows)
    rows = [
        '<section class="panel">',
        "<h2>Top Mod Script Blocks</h2>",
        "<p>Rows grouped by enclosing top-level script block, so shared files can be split into the action, trigger, scripted value, or building block that actually costs time.</p>",
        '<table class="data-table">',
        "<thead><tr><th>Block</th><th>Total</th><th>% Mod Total</th><th>Self</th><th>Bottleneck</th><th>Calls</th><th>Rows</th><th>Source</th></tr></thead>",
        "<tbody>",
    ]
    for impact in impacts[: result.top * 2]:
        rows.append(
            "<tr>"
            f"<td><code>{_h(impact.block)}</code></td>"
            f"<td>{_format_seconds(impact.value('total-time'))}</td>"
            f"<td>{_format_percent(_share(impact.value('total-time'), mod_totals['Total Time']))}</td>"
            f"<td>{_format_seconds(impact.value('self-time'))}</td>"
            f"<td>{_format_seconds(impact.value('bottleneck-time'))}</td>"
            f"<td>{_format_float(impact.value('call-count'))}</td>"
            f"<td>{_format_number(impact.row_count)}</td>"
            f"<td><code>{_h(impact.source_path)}</code></td>"
            "</tr>"
        )
    rows.extend(["</tbody>", "</table>", "</section>"])
    return "\n".join(rows)


def _render_html_file_section(
    mod_files: Sequence[FileAggregate],
    totals: Mapping[str, float],
    mod_totals: Mapping[str, float],
) -> str:
    rows = [
        '<section class="panel">',
        "<h2>All Mod Files Ranked By Total Impact</h2>",
        "<p>This table includes every resolved mod-owned file in the profiling export.</p>",
        '<table class="data-table">',
        "<thead><tr><th>File</th><th>Total Time</th><th>% All Total</th><th>% Mod Total</th><th>Bottleneck Time</th><th>% All Bottleneck</th><th>% Mod Bottleneck</th><th>Self Time</th><th>% Mod Self</th><th>Calls</th><th>% Mod Calls</th><th>Status</th></tr></thead>",
        "<tbody>",
    ]
    for aggregate in mod_files:
        rows.append(
            "<tr>"
            f"<td><code>{_h(aggregate.source_path)}</code></td>"
            f"<td>{_format_seconds(aggregate.value('total-time'))}</td>"
            f"<td>{_format_percent(_share(aggregate.value('total-time'), totals['Total Time']))}</td>"
            f"<td>{_format_percent(_share(aggregate.value('total-time'), mod_totals['Total Time']))}</td>"
            f"<td>{_format_seconds(aggregate.value('bottleneck-time'))}</td>"
            f"<td>{_format_percent(_share(aggregate.value('bottleneck-time'), totals['Bottleneck Time']))}</td>"
            f"<td>{_format_percent(_share(aggregate.value('bottleneck-time'), mod_totals['Bottleneck Time']))}</td>"
            f"<td>{_format_seconds(aggregate.value('self-time'))}</td>"
            f"<td>{_format_percent(_share(aggregate.value('self-time'), mod_totals['Self Time']))}</td>"
            f"<td>{_format_float(aggregate.value('call-count'))}</td>"
            f"<td>{_format_percent(_share(aggregate.value('call-count'), mod_totals['Call Count']))}</td>"
            f"<td>{_h(_source_status(aggregate.source))}</td>"
            "</tr>"
        )
    rows.extend(["</tbody>", "</table>", "</section>"])
    return "\n".join(rows)


def _render_html_blocking_rows_section(
    mod_rows: Sequence[ProfilingRow],
    totals: Mapping[str, float],
    mod_totals: Mapping[str, float],
) -> str:
    rows_by_bottleneck = sorted(
        mod_rows,
        key=lambda row: row.value("bottleneck-time"),
        reverse=True,
    )
    rows = [
        '<section class="panel">',
        "<h2>All Mod Rows Ranked By Blocking Pressure</h2>",
        "<p>This table includes every resolved mod-owned profiler row, sorted by Bottleneck Time so the strongest blocking-pressure signal is first.</p>",
        '<table class="data-table">',
        "<thead><tr><th>Profiler Row</th><th>Bottleneck Time</th><th>% All Bottleneck</th><th>% Mod Bottleneck</th><th>Total Time</th><th>% Mod Total</th><th>Self Time</th><th>% Mod Self</th><th>Calls</th><th>% Mod Calls</th><th>Status</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows_by_bottleneck:
        rows.append(
            "<tr>"
            f"<td><code>{_h(row.file_location)}</code></td>"
            f"<td>{_format_seconds(row.value('bottleneck-time'))}</td>"
            f"<td>{_format_percent(_share(row.value('bottleneck-time'), totals['Bottleneck Time']))}</td>"
            f"<td>{_format_percent(_share(row.value('bottleneck-time'), mod_totals['Bottleneck Time']))}</td>"
            f"<td>{_format_seconds(row.value('total-time'))}</td>"
            f"<td>{_format_percent(_share(row.value('total-time'), mod_totals['Total Time']))}</td>"
            f"<td>{_format_seconds(row.value('self-time'))}</td>"
            f"<td>{_format_percent(_share(row.value('self-time'), mod_totals['Self Time']))}</td>"
            f"<td>{_format_float(row.value('call-count'))}</td>"
            f"<td>{_format_percent(_share(row.value('call-count'), mod_totals['Call Count']))}</td>"
            f"<td>{_h(_source_status(row.source))}</td>"
            "</tr>"
        )
    rows.extend(["</tbody>", "</table>", "</section>"])
    return "\n".join(rows)


def _render_html_source_status_section(
    stale_mod_files: Sequence[FileAggregate],
    totals: Mapping[str, float],
    mod_totals: Mapping[str, float],
) -> str:
    rows = [
        '<section class="panel warning">',
        "<h2>Source Drift / Missing Source</h2>",
    ]
    if not stale_mod_files:
        rows.append("<p>No stale or missing mod-owned source references are present in the top report set.</p>")
    else:
        rows.append(
            "<p>These files still count toward impact, but current source files no longer fully match the captured profiler line references.</p>"
        )
        rows.append('<table class="data-table">')
        rows.append("<thead><tr><th>File</th><th>Status</th><th>Total Time</th><th>% Mod Total</th><th>Bottleneck Time</th><th>% Mod Bottleneck</th><th>Calls</th><th>% Mod Calls</th></tr></thead>")
        rows.append("<tbody>")
        for aggregate in stale_mod_files:
            rows.append(
                "<tr>"
                f"<td><code>{_h(aggregate.source_path)}</code></td>"
                f"<td>{_h(_source_status(aggregate.source))}</td>"
                f"<td>{_format_seconds(aggregate.value('total-time'))}</td>"
                f"<td>{_format_percent(_share(aggregate.value('total-time'), mod_totals['Total Time']))}</td>"
                f"<td>{_format_seconds(aggregate.value('bottleneck-time'))}</td>"
                f"<td>{_format_percent(_share(aggregate.value('bottleneck-time'), mod_totals['Bottleneck Time']))}</td>"
                f"<td>{_format_float(aggregate.value('call-count'))}</td>"
                f"<td>{_format_percent(_share(aggregate.value('call-count'), mod_totals['Call Count']))}</td>"
                "</tr>"
            )
        rows.extend(["</tbody>", "</table>"])
    rows.extend(["</section>"])
    return "\n".join(rows)


def _render_file_delta_table(
    before: AnalysisResult,
    after: AnalysisResult,
    *,
    title: str,
    top: int,
) -> list[str]:
    before_files = {
        aggregate.source_path: aggregate
        for aggregate in aggregate_files(before.rows)
        if aggregate.source.ownership == "mod"
    }
    after_files = {
        aggregate.source_path: aggregate
        for aggregate in aggregate_files(after.rows)
        if aggregate.source.ownership == "mod"
    }
    rows = []
    for source_path in sorted(set(before_files) | set(after_files)):
        before_value = before_files.get(source_path)
        after_value = after_files.get(source_path)
        rows.append(
            (
                source_path,
                before_value.value("total-time") if before_value else 0.0,
                after_value.value("total-time") if after_value else 0.0,
                before_value.value("call-count") if before_value else 0.0,
                after_value.value("call-count") if after_value else 0.0,
            )
        )
    rows.sort(key=lambda item: abs(item[2] - item[1]), reverse=True)
    lines = [
        f"## {title}",
        "",
        "| File | Total Before | Total After | Total Delta | Calls Before | Calls After | Calls Delta |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for source_path, before_total, after_total, before_calls, after_calls in rows[:top]:
        lines.append(
            "| {source} | {before_total} | {after_total} | {total_delta} | {before_calls} | {after_calls} | {call_delta} |".format(
                source=_escape_table(source_path),
                before_total=_format_float(before_total),
                after_total=_format_float(after_total),
                total_delta=_format_signed_float(after_total - before_total),
                before_calls=_format_float(before_calls),
                after_calls=_format_float(after_calls),
                call_delta=_format_signed_float(after_calls - before_calls),
            )
        )
    lines.append("")
    return lines


def _render_rural_capacity_delta_table(
    before: AnalysisResult,
    after: AnalysisResult,
    *,
    top: int,
) -> list[str]:
    before_callsites = {
        (callsite.capacity, callsite.surface, callsite.source_path): callsite
        for callsite in rural_capacity_callsite_rollups(before.rows)
    }
    after_callsites = {
        (callsite.capacity, callsite.surface, callsite.source_path): callsite
        for callsite in rural_capacity_callsite_rollups(after.rows)
    }
    rows = []
    for key in sorted(set(before_callsites) | set(after_callsites)):
        before_value = before_callsites.get(key)
        after_value = after_callsites.get(key)
        rows.append(
            (
                key,
                before_value.value("total-time") if before_value else 0.0,
                after_value.value("total-time") if after_value else 0.0,
                before_value.value("call-count") if before_value else 0.0,
                after_value.value("call-count") if after_value else 0.0,
            )
        )
    rows.sort(key=lambda item: abs(item[2] - item[1]), reverse=True)
    lines = [
        "## Rural Capacity Callsite Delta",
        "",
        "| Capacity | Surface | Source | Total Before | Total After | Total Delta | Calls Before | Calls After | Calls Delta |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for (capacity, surface, source_path), before_total, after_total, before_calls, after_calls in rows[:top]:
        lines.append(
            "| {capacity} | {surface} | {source} | {before_total} | {after_total} | {total_delta} | {before_calls} | {after_calls} | {call_delta} |".format(
                capacity=_escape_table(capacity),
                surface=_escape_table(surface),
                source=_escape_table(source_path),
                before_total=_format_float(before_total),
                after_total=_format_float(after_total),
                total_delta=_format_signed_float(after_total - before_total),
                before_calls=_format_float(before_calls),
                after_calls=_format_float(after_calls),
                call_delta=_format_signed_float(after_calls - before_calls),
            )
        )
    lines.append("")
    return lines


def _path_metadata(path: Path) -> Mapping[str, object]:
    metadata: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if path.exists():
        stat = path.stat()
        metadata.update(
            {
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }
        )
    return metadata


def _json_metric_totals(values: Mapping[str, float]) -> Mapping[str, float]:
    return {
        metric.key: values.get(metric.csv_name, 0.0)
        for metric in METRICS.values()
    }


def _performance_metadata(series: PerformanceSeries | None) -> Mapping[str, object] | None:
    if series is None:
        return None
    samples = series.samples
    return {
        "log": _path_metadata(series.path),
        "sample_count": series.sample_count,
        "duration_seconds": series.duration_seconds,
        "first_game_date": series.first_game_date,
        "last_game_date": series.last_game_date,
        "mean_average_delta": series.mean_average_delta,
        "min_delta": series.min_delta,
        "max_delta": series.max_delta,
        "estimated_frames_or_ticks": series.estimated_frames_or_ticks,
        "first_sample": _performance_sample_metadata(samples[0]) if samples else None,
        "last_sample": _performance_sample_metadata(samples[-1]) if samples else None,
        "extra_numeric_summary": series.extra_numeric_summary,
    }


def _performance_sample_metadata(sample: PerformanceSample) -> Mapping[str, object]:
    return {
        "total_time": sample.total_time,
        "average_delta": sample.average_delta,
        "min_delta": sample.min_delta,
        "max_delta": sample.max_delta,
        "game_date": sample.game_date,
        "extra_values": dict(sample.extra_values),
    }


def _metric_totals(rows: Sequence[ProfilingRow]) -> Mapping[str, float]:
    totals = _empty_metric_totals()
    for row in rows:
        for column in NUMERIC_COLUMNS:
            totals[column] += row.values.get(column, 0.0)
    return totals


def _sample_duration(samples: Sequence[PerformanceSample], index: int) -> float:
    if not samples:
        return 0.0
    if index + 1 < len(samples):
        return max(0.0, samples[index + 1].total_time - samples[index].total_time)
    if index > 0:
        return max(0.0, samples[index].total_time - samples[index - 1].total_time)
    return 0.0


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _clean_performance_key(key: object) -> str:
    return str(key or "").strip().strip('"')


def _html_stat_card(title: str, value: str, note: str) -> str:
    return "\n".join(
        [
            '<article class="card">',
            f"<h2>{_h(title)}</h2>",
            f'<p class="metric small">{_h(value)}</p>',
            f"<p>{note}</p>",
            "</article>",
        ]
    )


def _render_performance_svg(series: PerformanceSeries) -> str:
    samples = _downsample_performance_samples(series.samples, max_points=220)
    if len(samples) < 2:
        return "<p>Not enough samples to draw a performance graph.</p>"

    width = 1200
    height = 340
    pad_left = 70
    pad_right = 24
    pad_top = 20
    pad_bottom = 48
    plot_width = width - pad_left - pad_right
    plot_height = height - pad_top - pad_bottom
    start_time = samples[0].total_time
    end_time = samples[-1].total_time
    max_delta = max(max(sample.max_delta, sample.average_delta) for sample in samples)
    max_delta = max(max_delta, 0.001)

    def xy(total_time: float, delta: float) -> tuple[float, float]:
        x = pad_left + _safe_div(total_time - start_time, end_time - start_time) * plot_width
        y = pad_top + (1 - _safe_div(delta, max_delta)) * plot_height
        return x, y

    avg_points = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in (xy(sample.total_time, sample.average_delta) for sample in samples)
    )
    max_points = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in (xy(sample.total_time, sample.max_delta) for sample in samples)
    )
    y_labels = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        value = max_delta * fraction
        y = pad_top + (1 - fraction) * plot_height
        y_labels.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" y2="{y:.1f}" class="grid-line" />'
        )
        y_labels.append(
            f'<text x="{pad_left - 8}" y="{y + 4:.1f}" text-anchor="end">{_h(_format_delta(value))}</text>'
        )

    return "\n".join(
        [
            '<figure class="chart">',
            '<figcaption>Performance delta over elapsed run seconds</figcaption>',
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Average and maximum delta over elapsed seconds">',
            *y_labels,
            f'<line x1="{pad_left}" y1="{height - pad_bottom}" x2="{width - pad_right}" y2="{height - pad_bottom}" class="axis" />',
            f'<line x1="{pad_left}" y1="{pad_top}" x2="{pad_left}" y2="{height - pad_bottom}" class="axis" />',
            f'<polyline points="{max_points}" class="line max-delta" />',
            f'<polyline points="{avg_points}" class="line avg-delta" />',
            f'<text x="{pad_left}" y="{height - 14}">0 s</text>',
            f'<text x="{width - pad_right}" y="{height - 14}" text-anchor="end">{_h(_format_seconds(series.duration_seconds))}</text>',
            '<g class="chart-legend">',
            f'<text x="{pad_left + 12}" y="{pad_top + 18}">Average Delta</text>',
            f'<line x1="{pad_left}" y1="{pad_top + 14}" x2="{pad_left + 8}" y2="{pad_top + 14}" class="line avg-delta" />',
            f'<text x="{pad_left + 160}" y="{pad_top + 18}">MaxDelta</text>',
            f'<line x1="{pad_left + 140}" y1="{pad_top + 14}" x2="{pad_left + 152}" y2="{pad_top + 14}" class="line max-delta" />',
            "</g>",
            "</svg>",
            "</figure>",
        ]
    )


def _downsample_performance_samples(
    samples: Sequence[PerformanceSample],
    *,
    max_points: int,
) -> tuple[PerformanceSample, ...]:
    if len(samples) <= max_points:
        return tuple(samples)
    step = len(samples) / max_points
    return tuple(samples[min(len(samples) - 1, int(index * step))] for index in range(max_points))


def _ownership_metric_totals(
    rows: Sequence[ProfilingRow],
) -> Mapping[str, Mapping[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        ownership = row.source.ownership
        if ownership not in {"mod", "vanilla"}:
            ownership = "unknown"
        bucket = totals.setdefault(ownership, _empty_metric_totals())
        for column in NUMERIC_COLUMNS:
            bucket[column] += row.values.get(column, 0.0)
    return totals


def _empty_metric_totals() -> dict[str, float]:
    return {column: 0.0 for column in NUMERIC_COLUMNS}


def _mod_system_impacts(result: AnalysisResult) -> tuple[SystemImpact, ...]:
    buckets: dict[str, dict[str, object]] = {}
    for aggregate in aggregate_files(result.rows):
        if aggregate.source.ownership != "mod":
            continue
        key, name, note = _mod_system_bucket(aggregate.source_path)
        bucket = buckets.setdefault(
            key,
            {
                "name": name,
                "note": note,
                "row_count": 0,
                "file_count": 0,
                "values": _empty_metric_totals(),
                "top_file": None,
            },
        )
        bucket["row_count"] = int(bucket["row_count"]) + aggregate.row_count
        bucket["file_count"] = int(bucket["file_count"]) + 1
        values = bucket["values"]
        assert isinstance(values, dict)
        for column in NUMERIC_COLUMNS:
            values[column] += aggregate.values.get(column, 0.0)
        top_file = bucket["top_file"]
        if (
            top_file is None
            or aggregate.value("total-time") > top_file.value("total-time")
        ):
            bucket["top_file"] = aggregate

    impacts = []
    for key, bucket in buckets.items():
        values = bucket["values"]
        assert isinstance(values, dict)
        impacts.append(
            SystemImpact(
                key=key,
                name=str(bucket["name"]),
                blocking_note=str(bucket["note"]),
                row_count=int(bucket["row_count"]),
                file_count=int(bucket["file_count"]),
                values=dict(values),
                top_file=bucket["top_file"],
            )
        )
    return tuple(sorted(impacts, key=lambda impact: impact.value("total-time"), reverse=True))


def _mod_system_bucket(source_path: str) -> tuple[str, str, str]:
    if source_path in {
        "common/script_values/pp_building_caps.txt",
        "common/script_values/pp_building_cap_adjustments.txt",
    } or re.match(r"^common/script_values/pp_(farming|fishing|forest)_capacity\.txt$", source_path):
        return (
            "capacity_formulas",
            "Capacity Formulas",
            "Repeated max-level and resource-capacity formulas; these slow building availability, AI scans, and capacity decisions.",
        )
    if source_path == "common/scripted_triggers/pp_startup_building_compatibility.txt":
        return (
            "startup_compatibility",
            "Startup Compatibility Triggers",
            "Location compatibility checks for seeded or startup buildings; these can block setup and repeated candidate checks.",
        )
    if source_path.startswith("common/building_types/"):
        return (
            "building_definitions",
            "Building Definitions",
            "Building location_potential, allow, and max_levels checks; these block buildability scans and AI construction choices.",
        )
    if source_path == "common/employment_systems/pp_food_security_priorities.txt":
        return (
            "employment_priorities",
            "Employment Priorities",
            "Labor-priority math for food-security tags; these slow repeated employment allocation decisions.",
        )
    if source_path.startswith("common/on_action/"):
        return (
            "on_action_culling",
            "On-Action And Culling",
            "Periodic capacity setup and culling logic; these block scheduled tick/on-action processing when they fire.",
        )
    return (
        "other_mod_scripts",
        "Other Mod Scripts",
        "Resolved mod-owned cost outside the main known performance clusters.",
    )


def _rural_capacity_references(row: ProfilingRow) -> tuple[str, ...]:
    source_path = row.parsed_location.source_path
    for capacity, capacity_file in RURAL_CAPACITY_FILES.items():
        if source_path == capacity_file:
            return (capacity,)
    context = _row_context_text(row)
    return tuple(capacity for capacity in RURAL_CAPACITY_KEYS if capacity in context)


def _rural_capacity_surface(row: ProfilingRow) -> str:
    source_path = row.parsed_location.source_path
    if source_path in RURAL_CAPACITY_FILES.values():
        return "formula definition"
    if source_path.startswith("common/building_types/"):
        return _building_surface(row)
    if source_path.startswith("common/on_action/"):
        return "culling/on_action"
    if source_path.startswith("common/scripted_effects/"):
        return "scripted effect"
    if "/map_modes/" in source_path or source_path.startswith("gfx/map/map_modes/"):
        return "map mode"
    if source_path.startswith("common/script_values/"):
        return "scripted value"
    if source_path.startswith("common/scripted_triggers/"):
        return "scripted trigger"
    return row.parsed_location.kind or "other"


def _building_surface(row: ProfilingRow) -> str:
    target = _target_source_text(row)
    context = _row_context_text(row)
    if "max_levels" in target:
        return "max_levels"
    if any(capacity in target for capacity in RURAL_CAPACITY_KEYS):
        return "capacity gate"
    if "custom_tooltip" in target and any(capacity in context for capacity in RURAL_CAPACITY_KEYS):
        return "allow block"
    if re.match(r"^\s*allow\s*=", target):
        return "allow block"
    if "location_potential" in target or "location_potential" in context:
        return "location_potential"
    if "country_potential" in target or "country_potential" in context:
        return "country_potential"
    return "other"


def _building_name_from_source_path(source_path: str) -> str:
    name = Path(source_path).stem
    return name.removeprefix("zz_pp_").removeprefix("pp_")


def _top_level_script_blocks(path: Path) -> tuple[ScriptBlockRange, ...]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    blocks: list[ScriptBlockRange] = []
    depth = 0
    active_name: str | None = None
    active_start = 0
    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#", maxsplit=1)[0]
        if active_name is None and depth == 0:
            match = re.match(r"^\s*([^\s={]+)\s*=\s*\{", line)
            if match is not None:
                active_name = match.group(1)
                active_start = index
        depth += line.count("{") - line.count("}")
        if active_name is not None and depth <= 0:
            blocks.append(
                ScriptBlockRange(
                    name=active_name,
                    start_line=active_start,
                    end_line=index,
                )
            )
            active_name = None
            active_start = 0
            depth = 0
    return tuple(blocks)


def _script_block_for_line(blocks: Sequence[ScriptBlockRange], line: int) -> str:
    for block in blocks:
        if block.start_line <= line <= block.end_line:
            return block.name
    return "<file scope>"


def _target_source_text(row: ProfilingRow) -> str:
    for context_line in row.source.context:
        if context_line.target:
            return context_line.text.strip()
    return ""


def _row_context_text(row: ProfilingRow) -> str:
    if not row.source.context:
        return ""
    return "\n".join(context_line.text for context_line in row.source.context)


def _html_bar(
    label: str,
    value: float,
    max_value: float,
    detail: str,
    css_class: str,
) -> str:
    width = 0.0 if max_value <= 0 else max(4.0, value / max_value * 100)
    value_text = _format_seconds(value) if css_class in {"total", "block"} else _format_float(value)
    return (
        '<div class="bar-row">'
        f"<span>{_h(label)}</span>"
        '<div class="bar-track">'
        f'<div class="bar-fill {css_class}" style="width:{width:.3f}%"></div>'
        "</div>"
        f"<strong>{_h(value_text)}</strong>"
        f"<em>{_h(detail)}</em>"
        "</div>"
    )


def _html_css() -> str:
    return """
:root {
  color-scheme: light;
  --bg: #f6f3ee;
  --ink: #202124;
  --muted: #62666d;
  --line: #d8d2c8;
  --panel: #fffdf9;
  --mod: #b6463d;
  --vanilla: #2d6f8f;
  --unknown: #8b8175;
  --total: #386641;
  --block: #b6463d;
  --calls: #6a4c93;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.page { width: min(1440px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 48px; }
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 520px);
  gap: 24px;
  align-items: end;
  border-bottom: 1px solid var(--line);
  padding-bottom: 20px;
}
h1, h2, h3, p { margin-top: 0; }
h1 { font-size: clamp(30px, 4vw, 54px); line-height: 1; margin-bottom: 12px; }
h2 { font-size: 22px; margin-bottom: 8px; }
h3 { font-size: 15px; margin-bottom: 6px; }
p { color: var(--muted); }
code { font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace; font-size: 12px; }
.meta {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.meta div, .mini-stats div { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 8px; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; }
.cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 22px 0; }
.card, .panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 16px;
}
.card h2 { color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0; }
.metric { color: var(--ink); font-size: 30px; font-weight: 750; margin-bottom: 0; }
.metric.small { font-size: 24px; }
.share { color: var(--mod); font-weight: 750; margin-bottom: 8px; }
.panel { margin-top: 16px; overflow-x: auto; }
.warning { border-color: #d0a64f; }
.notes ul { margin: 8px 0 0 18px; padding: 0; color: var(--muted); }
.notes li { margin-bottom: 7px; }
.source-links a { color: var(--vanilla); }
.stack-list { display: grid; gap: 14px; }
.stack-row h3 { margin-bottom: 8px; }
.stack-bar {
  display: flex;
  width: 100%;
  height: 18px;
  overflow: hidden;
  background: #e8e1d8;
  border-radius: 999px;
}
.stack-bar span { display: block; min-width: 0; }
.mod, .swatch.mod { background: var(--mod); }
.vanilla, .swatch.vanilla { background: var(--vanilla); }
.unknown, .swatch.unknown { background: var(--unknown); }
.legend { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 8px; color: var(--muted); }
.legend.compact { font-size: 12px; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
.system-grid { display: grid; gap: 12px; }
.system-row {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(420px, 1.35fr) 210px;
  gap: 18px;
  align-items: start;
  padding: 14px 0;
  border-top: 1px solid var(--line);
}
.system-row:first-child { border-top: 0; }
.subtle { font-size: 12px; }
.bars { display: grid; gap: 8px; }
.bar-row {
  display: grid;
  grid-template-columns: 64px minmax(160px, 1fr) 82px 74px;
  gap: 8px;
  align-items: center;
}
.bar-track { height: 12px; background: #e8e1d8; border-radius: 999px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: inherit; }
.bar-fill.total { background: var(--total); }
.bar-fill.block { background: var(--block); }
.bar-fill.calls { background: var(--calls); }
.bar-row strong { text-align: right; }
.bar-row em { color: var(--muted); font-style: normal; font-size: 12px; text-align: right; }
.mini-stats { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.data-table { width: 100%; border-collapse: collapse; min-width: 1320px; }
.data-table th, .data-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
  text-align: right;
}
.data-table th:first-child, .data-table td:first-child { text-align: left; }
.data-table th { color: var(--muted); font-weight: 700; white-space: nowrap; }
.chart { margin: 18px 0 0; min-width: 760px; }
.chart figcaption { color: var(--muted); margin-bottom: 8px; }
.chart svg { width: 100%; height: auto; background: #fbf8f2; border: 1px solid var(--line); border-radius: 8px; }
.chart text { fill: var(--muted); font-size: 13px; }
.axis { stroke: #948c80; stroke-width: 1.2; }
.grid-line { stroke: #e5ded4; stroke-width: 1; }
.line { fill: none; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
.avg-delta { stroke: var(--vanilla); }
.max-delta { stroke: var(--mod); }
@media (max-width: 1000px) {
  .hero, .system-row, .cards { grid-template-columns: 1fr; }
  .bar-row { grid-template-columns: 58px minmax(120px, 1fr) 72px; }
  .bar-row em { display: none; }
}
""".strip()


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _share(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return value / total


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


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


def _format_seconds(value: float) -> str:
    return f"{_format_float(value)} s"


def _format_delta(value: float) -> str:
    return f"{_format_float(value)} s ({_format_float(value * 1000)} ms)"


def _format_float(value: float) -> str:
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _format_signed_float(value: float) -> str:
    formatted = _format_float(abs(value))
    if value > 0:
        return f"+{formatted}"
    if value < 0:
        return f"-{formatted}"
    return "0"


def _format_number(value: int) -> str:
    return f"{value:,}"


def _escape_table(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _is_windows_drive_path(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", text))
