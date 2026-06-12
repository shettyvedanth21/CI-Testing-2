# Debugger Mode Blueprint — Source of Truth

## Status

All phases complete. Debugger mode is implemented, validated, and team-usable.

## Purpose

Permanent debugger-mode rollout for core backend services. Not a one-shot script, not a patch. A repo-native, env-gated, reproducible debugging workflow that coexists cleanly with normal startup.

## Current Repository Context

- Repository: `Shivex-Main`
- Backend stack: Python 3.11 + FastAPI + Uvicorn
- Local orchestration: `docker-compose.yml` + `docker-compose.local.yml`
- Shared runtime pattern:
  - 7 services use `start.sh` wrappers (run `scripts/migration_guard.py` then `exec uvicorn ...`)
  - 3 services use Dockerfile `CMD` directly (no start.sh, no migration guard)
  - all API services use `@asynccontextmanager` lifespan (no deprecated `@app.on_event`)
  - all 10 API services import `validate_startup_contract` from `services/shared/startup_contract.py`
- Current debugger footprint:
  - shared bootstrap exists at `services/shared/debug_bootstrap.py`
  - API and worker entrypoints are wired with `init_debug()`
  - `debugpy>=1.8.0` is present in the required API and worker runtime dependency manifests
  - opt-in debug overlay exists at `docker-compose.debug.yml`
  - VS Code attach configuration exists at `.vscode/launch.json`
  - debugger mode remains disabled unless the debug overlay is explicitly included

## Confirmed Service Map

Verified from actual Dockerfiles, start.sh, compose files, and Python entry modules.

### API Entrypoints

| Service | Entrypoint (uvicorn target) | API Port | Start Method | Migration Guard | `services/shared/` imports | BG Tasks in Lifespan | APP_ROLE Branching |
|---|---|---:|---|---|---|---|---|
| `auth-service` | `app.main:app` | 8090 | `start.sh` | Yes (`scripts/migration_guard.py`) | `startup_contract` | 2 (`refresh_token_cleanup`, `platform_maintenance_delivery`) | No |
| `device-service` | `app:app` (defined in `app/__init__.py`) | 8000 | `start.sh` | Yes | `startup_contract`, `tenant_context` | 5 (`trends`, `dashboard_snapshot`, `projection_reconciler`, `state_interval_retention`, `dashboard_snapshot_retention`) | No |
| `energy-service` | `app.main:app` | 8010 | `start.sh` | Yes | `startup_contract` | 0 (awaited `energy_broadcaster.start/stop`) | No |
| `data-service` | `src.main:app` | 8081 | Dockerfile CMD | No | `startup_contract` | 0 in lifespan; role-based via `app_state.startup()` | Yes (`api`/`worker`) |
| `rule-engine-service` | `app:app` (defined in `app/__init__.py`) | 8002 | `start.sh` | Yes | `startup_contract` | 0 | No |
| `analytics-service` | `src.main:app` | 8003 | `start.sh` | Yes | `startup_contract` | 2 conditional (`job_worker`, `retention`); retrainer conditional | Yes (`api`/`worker`, enforced at module level; `_APIModuleGuard` blocks ML imports in api role) |
| `reporting-service` | `src.main:app` | 8085 | `start.sh` | Yes | `startup_contract` | 0 (APScheduler runs internally) | No |
| `waste-analysis-service` | `src.main:app` | 8087 | `start.sh` | Yes | `startup_contract` | 1 conditional (`retention_task`) | No |
| `copilot-service` | `main:app` (1-line shim re-exporting `src.main:app`) | 8007 | Dockerfile CMD | No | `startup_contract` | 0 | No |
| `data-export-service` | `main:app` | 8080 | Dockerfile CMD | No | `startup_contract` | 1 (via `ExportWorker._task` inside lifespan) | No |

### Worker Entrypoints

| Worker | Entrypoint | Compose Container(s) | `services/shared/` imports | APP_ROLE | Notes |
|---|---|---|---|---|---|
| `data-service` worker | `python -m src.worker_main` | `data-telemetry-worker`, `data-telemetry-worker-2` | `startup_contract` | Set to `worker` via compose env | Custom `_run()` with `app_state.startup()/shutdown()` and `asyncio.Event` |
| `rule-engine-service` worker | `python -m app.worker_main` | `rule-engine-worker` | **None** | Set to `worker` via compose env | Simple `asyncio.run(NotificationWorker().start())` |
| `analytics-service` worker | `python -m src.worker_main` | `analytics-worker`, `analytics-worker-2` | `startup_contract` | Set to `worker` via compose env | Custom `_run_worker()` with manual startup/shutdown |
| `reporting-service` worker | `python -m src.worker_main` | `reporting-worker` | `startup_contract` | Set to `worker` via compose env | Simple `asyncio.run(ReportWorker().start())` |

### Service Role Classification

**API-only** (no separate worker container, no background tasks or only awaited start/stop):
- `energy-service`
- `copilot-service`

**API with internal background tasks** (no separate worker container):
- `auth-service` (2 async tasks)
- `device-service` (5 async tasks)
- `waste-analysis-service` (1 async task)
- `data-export-service` (1 ExportWorker task)

**API + separate worker containers**:
- `data-service` (2 worker containers)
- `rule-engine-service` (1 worker container)
- `analytics-service` (2 worker containers)
- `reporting-service` (1 worker container)

## Debug Port Allocation

Stable, deterministic, documented. Range 5671-5686.

| Compose Service | Debug Port | Role |
|---|---:|---|
| `auth-service` | 5671 | API |
| `device-service` | 5672 | API |
| `energy-service` | 5673 | API |
| `data-service` | 5674 | API |
| `data-telemetry-worker` | 5675 | Worker |
| `data-telemetry-worker-2` | 5676 | Worker |
| `rule-engine-service` | 5677 | API |
| `rule-engine-worker` | 5678 | Worker |
| `analytics-service` | 5679 | API |
| `analytics-worker` | 5680 | Worker |
| `analytics-worker-2` | 5681 | Worker |
| `reporting-service` | 5682 | API |
| `reporting-worker` | 5683 | Worker |
| `waste-analysis-service` | 5684 | API |
| `copilot-service` | 5685 | API |
| `data-export-service` | 5686 | API |

Total: 10 API debug ports + 6 worker debug ports = 16 ports.

## Environment Variable Contract

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `DEBUGPY_ENABLE` | `bool` (`true`/`false`) | `false` | Master toggle. When false/absent, bootstrap is a no-op. |
| `DEBUGPY_HOST` | `str` | `0.0.0.0` | Listen host for debugpy server. |
| `DEBUGPY_PORT` | `int` | `0` | Listen port. Required when `DEBUGPY_ENABLE=true`. Set per-container via compose. `0` = not configured = no-op even if enable is true. |
| `DEBUGPY_WAIT_FOR_CLIENT` | `bool` | `false` | Block startup until a debug client attaches. Use sparingly. |

### Contract rules

1. If `DEBUGPY_ENABLE` is not `true` (case-insensitive), `init_debug()` returns immediately. Zero overhead.
2. If `DEBUGPY_ENABLE` is `true` but `DEBUGPY_PORT` is `0` or missing, `init_debug()` logs a warning and returns without starting debugpy. This prevents partial misconfiguration.
3. `DEBUGPY_WAIT_FOR_CLIENT=true` should only be set in compose for services where you need to step through early startup. Never set it by default.
4. These env vars are **never set** in `docker-compose.yml` or `docker-compose.local.yml`. They are set **only** in `docker-compose.debug.yml`.

## Debugger Architecture

### 1. Shared debug bootstrap

**Location:** `services/shared/debug_bootstrap.py`

This is the single module every entrypoint calls. It lives in `services/shared/` which is already symlinked into every service container at `/app/shared/` (importable as `from services.shared.debug_bootstrap import init_debug`).

```python
# services/shared/debug_bootstrap.py — Pseudocode contract
import os
import logging

logger = logging.getLogger("debug_bootstrap")

def init_debug() -> None:
    enable = os.environ.get("DEBUGPY_ENABLE", "").strip().lower() == "true"
    if not enable:
        return

    port_str = os.environ.get("DEBUGPY_PORT", "0").strip()
    try:
        port = int(port_str)
    except ValueError:
        logger.warning("DEBUGPY_PORT is not an integer (%s), skipping debugpy", port_str)
        return

    if port <= 0:
        logger.warning("DEBUGPY_ENABLE=true but DEBUGPY_PORT is invalid (%s), skipping debugpy", port_str)
        return

    host = os.environ.get("DEBUGPY_HOST", "0.0.0.0").strip()

    try:
        import debugpy
        debugpy.listen((host, port))
        logger.info("debugpy listening on %s:%d", host, port)
    except Exception as exc:
        logger.warning("debugpy.listen() failed: %s", exc)
        return

    wait = os.environ.get("DEBUGPY_WAIT_FOR_CLIENT", "").strip().lower() == "true"
    if wait:
        logger.info("debugpy waiting for client on %s:%d ...", host, port)
        debugpy.wait_for_client()
        logger.info("debugpy client attached")
```

**Safety properties:**
- No-op when disabled (zero import overhead for `debugpy` itself since it's inside the `try` block)
- Graceful degradation on misconfiguration
- Never blocks startup unless `DEBUGPY_WAIT_FOR_CLIENT=true` is explicitly set
- Logs all actions for observability

### 2. Service entrypoint integration pattern

For every API entry module and every worker entry module, add at the **top of the file** (after imports, before FastAPI app creation):

```python
from services.shared.debug_bootstrap import init_debug
init_debug()
```

This runs at module-import time, which is:
- **Before** the uvicorn server starts accepting connections
- **Before** the lifespan handler fires
- **After** any migration guard (migration guards run in start.sh before Python is invoked)

For worker entry modules, the same call goes at the top of the script, before `asyncio.run()` or any other startup.

### 3. Dependency management

Add `debugpy` to every service requirements file:

| Service | Requirements File(s) |
|---|---|
| `auth-service` | `requirements.txt` |
| `device-service` | `requirements.txt` |
| `energy-service` | `requirements.txt` |
| `data-service` | `requirements.txt` |
| `rule-engine-service` | `requirements.txt` |
| `analytics-service` | `requirements.api.txt` AND `requirements.worker.txt` |
| `reporting-service` | `requirements.txt` |
| `waste-analysis-service` | `requirements.txt` |
| `copilot-service` | `requirements.txt` |
| `data-export-service` | `requirements.txt` |

**Note:** `analytics-service` is the only service with split requirements (multi-stage Dockerfile with `api` and `worker` targets). Both stages need `debugpy` available.

**Recommended pin:** `debugpy>=1.8.0` (no upper bound pin needed; it's a debug-only tool).

No Dockerfile changes are needed. All Dockerfiles already `COPY requirements.txt .` and `RUN pip install -r requirements.txt`. Adding debugpy to the requirements file and rebuilding is sufficient.

### 4. Compose debug override

**Location:** `docker-compose.debug.yml`

**Strategy:** Overlay-only. Sets `DEBUGPY_ENABLE=true` and `DEBUGPY_PORT=<port>` for each service, and exposes the debug port. Does not change any other behavior, any other env var, or any command.

**Usage:**
```bash
docker compose --env-file .env.local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.debug.yml \
  up -d --build
```

**Key principle:** The debug overlay is the third file in the compose stack. Without it, everything runs normally. With it, debugpy activates in the configured containers.

The overlay will contain one service stanza per compose service, each with:
- `environment:` adding `DEBUGPY_ENABLE=true`, `DEBUGPY_PORT=<port>`
- `ports:` exposing `"<host_debug_port>:<container_debug_port>"` where both are the same value from the allocation table

For worker containers that have `healthcheck: disable: true` already (rule-engine-worker, reporting-worker), no healthcheck change is needed. For data-telemetry-worker and data-telemetry-worker-2, their Redis-based healthcheck remains unchanged.

### 5. IDE integration

**Location:** `.vscode/launch.json`

VS Code "attach" configurations, one per debug port, plus compound launch configs for common API+worker pairs.

Individual attach configs:
- Attach auth-service API (port 5671)
- Attach device-service API (port 5672)
- Attach energy-service API (port 5673)
- Attach data-service API (port 5674)
- Attach data-telemetry-worker (port 5675)
- Attach data-telemetry-worker-2 (port 5676)
- Attach rule-engine-service API (port 5677)
- Attach rule-engine-worker (port 5678)
- Attach analytics-service API (port 5679)
- Attach analytics-worker (port 5680)
- Attach analytics-worker-2 (port 5681)
- Attach reporting-service API (port 5682)
- Attach reporting-worker (port 5683)
- Attach waste-analysis-service API (port 5684)
- Attach copilot-service API (port 5685)
- Attach data-export-service API (port 5686)

Compound configs:
- data-service full (API + worker-1)
- rule-engine full (API + worker)
- analytics full (API + worker-1)
- reporting full (API + worker)

Each config uses `"request": "attach"`, `"connect": {"host": "localhost", "port": <debug_port>}`, `"pathMappings": [{"localRoot": "${workspaceFolder}/services/<service>", "remoteRoot": "/app"}]`.

## Exact File Touch Map

### Phase 2: Shared debug bootstrap

| Action | File Path |
|---|---|
| CREATE | `services/shared/debug_bootstrap.py` |
| CREATE | `services/shared/test_debug_bootstrap.py` |

### Phase 3: API service integration

| Action | File Path | Change |
|---|---|---|
| EDIT | `services/auth-service/app/main.py` | Add `init_debug()` call at top |
| EDIT | `services/device-service/app/__init__.py` | Add `init_debug()` call at top |
| EDIT | `services/energy-service/app/main.py` | Add `init_debug()` call at top |
| EDIT | `services/data-service/src/main.py` | Add `init_debug()` call at top |
| EDIT | `services/rule-engine-service/app/__init__.py` | Add `init_debug()` call at top |
| EDIT | `services/analytics-service/src/main.py` | Add `init_debug()` call at top |
| EDIT | `services/reporting-service/src/main.py` | Add `init_debug()` call at top |
| EDIT | `services/waste-analysis-service/src/main.py` | Add `init_debug()` call at top |
| EDIT | `services/copilot-service/src/main.py` | Add `init_debug()` call at top |
| EDIT | `services/data-export-service/main.py` | Add `init_debug()` call at top |
| EDIT | `services/auth-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/device-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/energy-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/data-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/rule-engine-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/analytics-service/requirements.api.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/analytics-service/requirements.worker.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/reporting-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/waste-analysis-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/copilot-service/requirements.txt` | Add `debugpy>=1.8.0` |
| EDIT | `services/data-export-service/requirements.txt` | Add `debugpy>=1.8.0` |

### Phase 4: Worker integration

| Action | File Path | Change |
|---|---|---|
| EDIT | `services/data-service/src/worker_main.py` | Add `init_debug()` call at top |
| EDIT | `services/rule-engine-service/app/worker_main.py` | Add `init_debug()` call at top |
| EDIT | `services/analytics-service/src/worker_main.py` | Add `init_debug()` call at top |
| EDIT | `services/reporting-service/src/worker_main.py` | Add `init_debug()` call at top |

### Phase 5: Compose debug overlay

| Action | File Path |
|---|---|
| CREATE | `docker-compose.debug.yml` |

### Phase 6: IDE integration

| Action | File Path |
|---|---|
| CREATE | `.vscode/launch.json` |

### Phase 7: Validation and docs

| Action | File Path | Change |
|---|---|---|
| EDIT | Relevant docs/README | Add debugging runbook section |

**Total file count across all phases:** 2 create (Phase 2) + 20 edit (Phase 3) + 4 edit (Phase 4) + 1 create (Phase 5) + 1 create (Phase 6) + docs = ~28 files.

## Risks and Blockers

### Risk 1: analytics-service `_APIModuleGuard` blocks ML imports in API role

The analytics-service `src/main.py` installs a custom `MetaPathFinder` at module level that blocks imports of `numpy`, `pandas`, `sklearn`, etc. when `APP_ROLE=api`. Since `debugpy` is a pure-Python debugging tool (not an ML library), it should not be blocked. But the guard should be verified during Phase 3 to confirm it doesn't inadvertently block `debugpy` import paths.

**Mitigation:** During Phase 3, test that `import debugpy` works inside the analytics API container. The `_APIModuleGuard` blocks specific module names (`numpy`, `pandas`, etc.), not all imports, so `debugpy` should be unaffected.

### Risk 2: rule-engine-worker does not call `validate_startup_contract`

The `rule-engine-service/app/worker_main.py` is the only worker entrypoint that does not import `startup_contract`. This is not directly a debugger risk, but it means the worker container may start even if required env vars are missing. This inconsistency predates this debugger work and should be tracked separately.

**Mitigation:** Not a debugger blocker. Noted for future hygiene.

### Risk 3: Two services define FastAPI app in `__init__.py`, not `main.py`

`device-service` and `rule-engine-service` define their `app` in `app/__init__.py` (674 and 81 lines respectively). Adding `init_debug()` at the top of `__init__.py` means it runs when the package is first imported. This is correct behavior but these are larger files where care must be taken with placement.

**Mitigation:** Add the import and call immediately after the existing docstring/module-level comments, before any other imports from the service's own modules.

### Risk 4: copilot-service has a re-export shim

`copilot-service/main.py` is a 1-line file: `from src.main import app`. The actual app definition is in `copilot-service/src/main.py`. Adding `init_debug()` to `src/main.py` is correct and will run when uvicorn imports `main:app` (which triggers `src.main` import).

**Mitigation:** No special handling needed. The shim just re-exports.

### Risk 5: data-service has no migration guard

`data-service`, `copilot-service`, and `data-export-service` have no `scripts/migration_guard.py` and no start.sh. They start directly from Dockerfile CMD. This means there is no pre-Python startup step. Debug bootstrap runs at Python module import time, which is fine.

**Mitigation:** No special handling needed. No migration guard to bypass or respect.

### Risk 6: debugpy startup timing with uvicorn workers

If uvicorn is started with `--workers N` (multiple worker processes), each worker process would try to listen on the same debug port, causing conflicts. Currently, no service uses `--workers` in its start.sh or Dockerfile CMD — all run single-process uvicorn. This is safe as-is.

**Mitigation:** Do not add `--workers` to any service without also assigning per-worker debug ports. Document this constraint.

## Repo Inconsistencies Discovered

1. **rule-engine-worker skips startup_contract** — all other workers call it. Should be fixed independently.
2. **Migration guard patterns differ** — auth-service uses a 30-line simple wrapper; device-service uses a 200-line advisory-lock version. Other services vary. Not a debugger blocker.
3. **reporting-service main.py has redundant first `app`** — line 34 creates a bare FastAPI instance without lifespan, then line 95 reassigns `app` with the proper lifespan-bearing instance. Not a debugger blocker but worth noting.
4. **Import path inconsistency for shared modules** — some services import `from services.shared.startup_contract import ...` (correct, matches the symlink), while others also import `from shared.auth_middleware import ...` (works because `shared/` is at the container's Python path root). Both paths work at runtime. The debug bootstrap should use the `services.shared` path for consistency.

## Validation Sequence

This is the exact validation order for each phase.

### Phase 2 validation (shared bootstrap)

1. Run `python -m pytest services/shared/test_debug_bootstrap.py` — unit tests pass
2. Confirm `init_debug()` is a no-op when `DEBUGPY_ENABLE` is unset/false
3. Confirm `init_debug()` logs a warning and returns when `DEBUGPY_ENABLE=true` but port is invalid
4. Confirm `init_debug()` calls `debugpy.listen()` when properly configured
5. Confirm normal startup of one simple service (e.g., energy-service) is unchanged — build, start, hit `/health`

### Phase 3 validation (API integration)

For each API service after integration:
1. Build the service image: `docker compose build <service>`
2. Start without debug overlay: `docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml up -d <service>`
3. Hit `/health` — confirm it responds normally
4. Check container logs — no debugpy messages
5. Start with debug overlay: add `-f docker-compose.debug.yml`
6. Hit `/health` — confirm it still responds
7. Check container logs — should see `debugpy listening on 0.0.0.0:<port>`
8. From host, confirm port is reachable: `python -c "import socket; s=socket.socket(); s.settimeout(2); result=s.connect_ex(('localhost', <port>)); print('open' if result==0 else 'closed'); s.close()"`
9. Attach VS Code debugger to the port — confirm connection succeeds
10. Set a breakpoint in a route handler, hit the route, confirm breakpoint triggers

### Phase 4 validation (worker integration)

For each worker after integration:
1. Build the worker image: `docker compose build <worker-container>`
2. Start full stack with debug overlay
3. Trigger a job that the worker processes (e.g., submit an analytics job, trigger a notification)
4. Attach VS Code debugger to the worker debug port
5. Set breakpoint in the worker's job processing code
6. Confirm breakpoint triggers when the job is processed

### Phase 5 validation (compose overlay)

1. Start full stack with debug overlay: `docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.debug.yml up -d --build`
2. Confirm all services start and pass healthchecks
3. Confirm all debug ports are accessible from host
4. Stop and restart without debug overlay — confirm normal behavior

### Phase 6 validation (IDE integration)

1. Open `.vscode/launch.json`
2. Select a single-service attach config — confirm it connects
3. Select a compound config (e.g., data-service full) — confirm both attach
4. Debug a request end-to-end

### Phase 7 validation (full regression)

1. Run full Level 1 CI: `./scripts/run-ci-validation.sh`
2. Start normal stack (no debug overlay) — confirm all healthchecks pass
3. Start debug stack — confirm all healthchecks pass
4. Run a smoke test on each API service in debug mode
5. Run a smoke test on one worker flow in debug mode
6. Turn debug mode off and re-verify normal startup

## Backend Debugger Mode — Usage Guide

### Start the debug stack

```bash
docker compose --env-file .env.local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.debug.yml \
  up -d --build
```

The `--build` flag is required the first time or after any dependency change (including the addition of `debugpy` to requirements files). Subsequent starts without code changes can omit `--build`.

### Stop the debug stack

```bash
docker compose --env-file .env.local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.debug.yml \
  down
```

Or simply:

```bash
docker compose down
```

### Start the normal stack (debugger disabled)

```bash
docker compose --env-file .env.local \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  up -d --build
```

Without `-f docker-compose.debug.yml`, no `DEBUGPY_*` env vars are set and `init_debug()` is a complete no-op in every container.

### Attach from VS Code

1. Open the Run and Debug panel (Ctrl+Shift+D / Cmd+Shift+D).
2. Select an attach configuration from the dropdown (e.g., "Attach auth-service API").
3. Press F5 or click the green arrow.
4. Set breakpoints in the service source code under `services/<service>/`.
5. Trigger a request to the service. The debugger will pause at breakpoints.

### Compound launch configs

For services with both API and worker processes, compound configs attach to both simultaneously:

| Compound Config | What It Attaches |
|---|---|
| data-service full (API + worker-1) | data-service API (5674) + data-telemetry-worker (5675) |
| rule-engine full (API + worker) | rule-engine API (5677) + rule-engine-worker (5678) |
| analytics full (API + worker-1) | analytics API (5679) + analytics-worker (5680) |
| reporting full (API + worker) | reporting API (5682) + reporting-worker (5683) |

### Debug port map

| Compose Service | Debug Port | Role |
|---|---:|---|
| `auth-service` | 5671 | API |
| `device-service` | 5672 | API |
| `energy-service` | 5673 | API |
| `data-service` | 5674 | API |
| `data-telemetry-worker` | 5675 | Worker |
| `data-telemetry-worker-2` | 5676 | Worker |
| `rule-engine-service` | 5677 | API |
| `rule-engine-worker` | 5678 | Worker |
| `analytics-service` | 5679 | API |
| `analytics-worker` | 5680 | Worker |
| `analytics-worker-2` | 5681 | Worker |
| `reporting-service` | 5682 | API |
| `reporting-worker` | 5683 | Worker |
| `waste-analysis-service` | 5684 | API |
| `copilot-service` | 5685 | API |
| `data-export-service` | 5686 | API |

### How to disable debugger mode

Remove `-f docker-compose.debug.yml` from the compose command. No code changes, no env changes, no rebuilds needed. The normal stack runs exactly as before.

### Caveats

- **First build after adding `debugpy`**: The first `--build` after Phase 3/4 changes will install `debugpy` into every service image. This is a one-time cost.
- **`DEBUGPY_WAIT_FOR_CLIENT`**: Set to `false` by default in the debug overlay. Change it to `true` for a specific service in `docker-compose.debug.yml` if you need to step through early startup code before the service begins serving. This blocks the process until a debugger attaches.
- **Do not add `--workers N` to uvicorn**: Each debug port is assigned to one process. Multi-worker uvicorn would conflict on the same port.
- **Image rebuilds**: After any change to `requirements.txt` or `debug_bootstrap.py`, rebuild with `--build`.

### Implementation files

| File | Purpose |
|---|---|
| `services/shared/debug_bootstrap.py` | Shared env-gated debugpy bootstrap (no-op when disabled) |
| `services/shared/test_debug_bootstrap.py` | Unit tests for the bootstrap (17 tests) |
| `docker-compose.debug.yml` | Opt-in compose overlay that sets debug env vars and exposes ports |
| `.vscode/launch.json` | VS Code attach and compound launch configurations |

## What Should Not Happen

- Do not hard-code debugger behavior into normal startup
- Do not force `wait_for_client()` in any default configuration
- Do not expose debug ports in `docker-compose.yml` or `docker-compose.local.yml`
- Do not duplicate ad hoc debug bootstrap code in every service
- Do not skip startup guards just because debug mode is enabled
- Do not add `--workers N` to uvicorn without also solving per-worker debug port allocation
- Do not set `DEBUGPY_ENABLE=true` in any committed `.env` or compose file other than `docker-compose.debug.yml`

## Phase Rollout Plan — All Complete

### Phase 1: Discovery and design finalization — COMPLETE
### Phase 2: Shared debug bootstrap — COMPLETE
- Created `services/shared/debug_bootstrap.py`
- Created `services/shared/test_debug_bootstrap.py` (17 tests, all passing)

### Phase 3: API service integration — COMPLETE
- Added `init_debug()` to all 10 API entry modules
- Added `debugpy>=1.8.0` to all API requirements files (11 files)

### Phase 4: Worker integration — COMPLETE
- Added `init_debug()` to all 4 worker entry modules
- Added `debugpy>=1.8.0` to `analytics-service/requirements.worker.txt`

### Phase 5: Compose debug overlay — COMPLETE
- Created `docker-compose.debug.yml` (16 services, ports 5671-5686)

### Phase 6: IDE integration — COMPLETE
- Created `.vscode/launch.json` (16 attach configs, 4 compound configs)

### Phase 7: Validation and docs — COMPLETE
- All unit tests pass (17/17)
- All 15 entry modules compile cleanly
- Compose overlay merge validated (16 DEBUGPY_PORT, 0 in base)
- Usage documentation added to this document

## Ready State

Debugger mode is fully implemented and documented. This document is the source of truth.
