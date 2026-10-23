# Local-only material

Backup audit: 2026-10-23. Originals remain in place. No secret values are recorded.

| Path | Approximate size | Why excluded | Reproducible / external backup |
|---|---:|---|---|
| `workspace/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `.pytest_cache/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `runs/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `.venv/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `validation/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `release/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `workspace_det/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `journal/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `reports/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `.env` | 0.00 MiB | secret-bearing filename, runtime/database, generated or archive format | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `debug.pid` | 0.00 MiB | selective consolidation: runtime, history container, private export or ambiguous artifact | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `debug.log` | 0.00 MiB | secret-bearing filename, runtime/database, generated or archive format | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `benchmark_full_run.log` | 0.00 MiB | secret-bearing filename, runtime/database, generated or archive format | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `failed_coder_raw.json` | 0.00 MiB | selective consolidation: runtime, history container, private export or ambiguous artifact | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `failed_coder_repaired.json` | 0.00 MiB | selective consolidation: runtime, history container, private export or ambiguous artifact | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `opencod-kimie.json` | 0.00 MiB | selective consolidation: runtime, history container, private export or ambiguous artifact | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `opencode.json` | 0.00 MiB | selective consolidation: runtime, history container, private export or ambiguous artifact | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `openforge/__pycache__/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `openforge/agents/__pycache__/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `openforge/services/__pycache__/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `openforge/benchmarks/__pycache__/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `tests/__pycache__/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |
| `scripts/__pycache__/` | directory; see local audit | generated/local-only/nested or selective consolidation exclusion | Dependencies/builds: usually reproducible; credentials, source snapshots and databases: retain encrypted external backup as needed. |

Exact selection and SHA evidence: `/home/zhufan/project-reorg-report-20261023/`. Generated trees were excluded as whole directories. No live database/service configuration was changed.
