# Prosper Or Perish Profiling

Read-only profiling log analyzer for Europa Universalis V logs, built first for
Prosper or Perish performance work.

The initial tool focuses on:

- `logs/profiling_roots.csv`
- hotspot ranking by total time, self time, bottleneck time, calls, and averages
- source context lookup against live mod folders, Constructor checkout folders, or vanilla EU5
- Markdown reports that keep historical profiler findings even when current source files drift

It does not edit game files, live mods, Constructor files, or logs.

## Usage

From this repository:

```bash
uv run pp-profile profiling-roots analyze
```

For normal Prosper or Perish profiling work, use the one-command workflow:

```bash
uv run pp-profile profiling-roots latest
```

`latest` reads `pp-profile.local.toml` or `pp-profile.toml` when present,
copies the current live EU5 logs into `reports/captures/<timestamp>/`, writes
timestamped Markdown/HTML/JSON reports, updates run history, and diffs against
the previous frozen capture by default. Use `--no-diff-previous` when you only
want the fresh report.

Example local config:

```toml
[profiling_roots]
user_data_root = "/mnt/c/Users/Anwender/Documents/Paradox Interactive/Europa Universalis V"
load_order = "../ProsperOrPerishConstructor/constructor.load_order.toml"
report_prefix = "rural_capacity_profile"
top = 100
diff_previous = true
```

`pp-profile.local.toml` is ignored by git, so machine-local paths stay local.

By default, the command searches standard EU5 user-data locations, including WSL
paths such as:

```text
/mnt/c/Users/*/Documents/Paradox Interactive/Europa Universalis V
```

Write the report to an explicit path:

```bash
uv run pp-profile profiling-roots analyze \
  --output reports/profiling_roots.md
```

Write a standalone HTML visualization alongside the Markdown report:

```bash
uv run pp-profile profiling-roots analyze \
  --output reports/profiling_roots.md \
  --html-output reports/profiling_roots.html \
  --json-output reports/profiling_roots.metadata.json
```

The HTML view breaks the whole log down by total impact, Bottleneck Time
blocking pressure, call volume, ownership, and mod-owned systems such as
building capacity formulas, building definitions, employment priorities, and
on-action/culling logic. Its mod file and mod row drill-down tables include all
resolved mod-owned entries from the export, with seconds plus percent-of-all
and percent-of-mod columns. When `logs/performance_degradation.log` is present
next to the profiling CSV, the reports also include a Run Speed section with
capture elapsed seconds, gameplay elapsed seconds, game-day span, seconds per
game day, game days per second, frame/update-delta statistics, and an inline
HTML graph. Bootstrap rows such as `1_01_01` are ignored for gameplay-speed
metrics. Markdown and HTML reports also include rural-capacity callsite rollups,
building surface breakdowns, fruit-orchard focus tables, and likely duplicate
`max_levels`/`allow` capacity evaluations. The JSON sidecar stores
machine-readable run metadata, including CSV file stats, performance-log sample
count, elapsed seconds, estimated frames/updates, game-date speed, memory, GUI
widget, and ECS summaries when those columns are present.

Use a non-adjacent performance log explicitly:

```bash
uv run pp-profile profiling-roots analyze \
  --csv reports/current/profiling_roots.csv \
  --performance-log reports/current/performance_degradation.log \
  --output reports/current/profiling_roots.md
```

When a Markdown report is written to a file, analysis also updates run history
at these default paths:

```text
reports/profiling_run_history.jsonl
reports/profiling_run_history.md
```

Override or disable history tracking:

```bash
uv run pp-profile profiling-roots analyze \
  --history-output reports/profiling_run_history.jsonl \
  --history-report-output reports/profiling_run_history.md

uv run pp-profile profiling-roots analyze --no-history
```

Compare two profiling captures:

```bash
uv run pp-profile profiling-roots diff \
  --before-csv reports/before/profiling_roots.csv \
  --before-performance-log reports/before/performance_degradation.log \
  --after-csv reports/after/profiling_roots.csv \
  --after-performance-log reports/after/performance_degradation.log \
  --output reports/profiling_roots_diff.md
```

Analyze a specific CSV:

```bash
uv run pp-profile profiling-roots analyze \
  --csv "/mnt/c/Users/Anwender/Documents/Paradox Interactive/Europa Universalis V/logs/profiling_roots.csv"
```

Resolve source context from a Constructor load-order file:

```bash
uv run pp-profile profiling-roots analyze \
  --load-order /home/jan/development/ProsperOrPerishConstructor/constructor.load_order.toml
```

Add source roots manually:

```bash
uv run pp-profile profiling-roots analyze \
  --mod-root "/mnt/c/Users/Anwender/Documents/Paradox Interactive/Europa Universalis V/mod/Prosper or Perish (Population Growth & Food Rework)" \
  --vanilla-root "C:\Games\steamapps\common\Europa Universalis V"
```

Rank by a different metric:

```bash
uv run pp-profile profiling-roots analyze --metric self-time --top 25
```

## Commands

```bash
uv run pp-profile --help
uv run pp-profile profiling-roots --help
uv run pp-profile profiling-roots analyze --help
```

## Development

```bash
uv sync --dev
uv run pytest
```
