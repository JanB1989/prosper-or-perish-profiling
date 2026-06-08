# Prosper Or Perish Profiling

## Repository Workflow

- Use `uv run pp-profile --help` as the canonical command index for this tool.
- Keep V1 analysis read-only: do not edit live EU5 logs, Constructor files, or mod files.
- Generated reports belong in ignored `reports/` or `artifacts/`.
- Do not commit real game logs, machine-local paths, or generated profiling output.
- Windows paths such as `C:\Users\Anwender\Documents\Paradox Interactive\Europa Universalis V` should be accepted by the CLI and normalized under WSL/Linux when needed.
- Use explicit fixture data in tests rather than copying real Paradox log files into the repository.

## Analysis Style

- Preserve profiling findings even when source files have changed since the log was captured.
- Mark stale, missing, unknown, or out-of-range source references clearly instead of hiding them.
- Treat recommendations as diagnostic next actions. Do not generate or apply gameplay patches from this repository.
