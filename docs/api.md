# Crucible HTTP API

A **read-first** HTTP surface over everything a Crucible run produces. It fans in
three sources:

| Source | Holds |
|---|---|
| `findings.sqlite` (domain store) | runs, findings + provenance + funnel status, per-pass validation verdicts, wishlist, tool-usage counters |
| `checkpoints.sqlite` (LangGraph `SqliteSaver`) | execution state — `pending_hunts`, `completed_cells`, `cycle_count`, `recon_quality`, `subsystems`, current node |
| `<workspace>/` (git commit per node, §7) | `architecture.md`, `report.json` / `report.md`, `recon/*.json`, `coverage/*.md`, `dedup/clusters.json`, `findings/*.json`, `offload/*.txt`, `run.log` |

It makes **no outbound network calls** — it only reads those two DB files and the
workspace tree (specs §13).

Design tenets: every list endpoint is paginated (`?limit=`, `?offset=`, default
100 / max 1000) and returns `{items, total, limit, offset}`; errors are always
`{error, detail}` with no traceback; the finding payload shape is exactly what
the Hunt node wrote (`crucible/validation/schema.py`).

---

## Run it

```bash
pip install -e ".[api]"          # adds fastapi + uvicorn
crucible serve                   # http://127.0.0.1:8787  — docs at /docs
```

`crucible serve` flags (all also settable by env):

| Flag | Env | Default |
|---|---|---|
| `--host` | `CRUCIBLE_API_HOST` | `127.0.0.1` |
| `--port` | `CRUCIBLE_API_PORT` | `8787` |
| `--store-url` | `CRUCIBLE_API_STORE_URL` / `CRUCIBLE_STORE_URL` | `sqlite:///findings.sqlite` |
| `--checkpoint-db` | `CRUCIBLE_API_CHECKPOINT_DB` | `checkpoints.sqlite` |
| `--workspace-root` | `CRUCIBLE_API_WORKSPACE_ROOT` | `.crucible-workspace` |
| `--no-auth` | `CRUCIBLE_API_ALLOW_NO_AUTH=1` | off |
| `--reload` | — | off |
| — | `CRUCIBLE_API_TOKEN` | *(unset — no auth)* |
| — | `CRUCIBLE_API_TOKEN_READONLY` | *(unset)* |
| — | `CRUCIBLE_API_CORS_ORIGINS` | *(none)* |

The API resolves a run's workspace from the `workspace_path` recorded on its
`runs` row; runs created before that column existed fall back to
`--workspace-root`.

### Dashboard

If `ui/dist` exists (`npm --prefix ui install && npm --prefix ui run build`),
`crucible serve` also serves the web dashboard at `/`. Override the location with
`CRUCIBLE_API_UI_DIR`, or run API-only with `--no-ui` / `CRUCIBLE_API_NO_UI=1`.
Browser navigations to client routes (`Accept: text/html`) fall back to
`index.html`; the SPA's own `fetch()` calls hit the API paths below unchanged.

---

## Endpoints

Interactive reference: **`/docs`** (Swagger UI), raw schema **`/openapi.json`**
(also committed at `tests/data/openapi.json`).

### Runs, report, metrics, coverage

| Method & path | Notes |
|---|---|
| `POST /runs` | **trigger a run** — clone a repo and launch `crucible run` (see [Triggering runs](#triggering-runs) below) |
| `GET /runs` | filters: `repo` (substring), `outcome`, `language`, `since` (ISO-8601). Newest first. |
| `GET /runs/{run_id}` | detail + funnel `counts` (`raw`, `mechanical_failed`, `bug_upheld`, `reach_upheld`, `duplicate`, … + `total`), plus `source_spec` / `clone_status` / `clone_error` for an API-triggered run |
| `GET /runs/{run_id}/report` | parsed `report.json`; `404` if the report node hasn't run |
| `GET /runs/{run_id}/report.md` | `text/plain` |
| `GET /runs/{run_id}/metrics` | funnel counts, `fork_rate` (`forks/hunt_exec`), per-tool usage, and `cycles`/`continuations`/`token_spend` (from the report when present) |
| `GET /runs/{run_id}/coverage` | `(area × attack_class)` matrix — per cell `passes`, `findings`, `productive`, `shallow` — plus `gapfill_buckets` (`failed` / `missing` / `barren`) |

### Findings & validations

| Method & path | Notes |
|---|---|
| `GET /runs/{run_id}/findings` | filters: `status` (repeatable), `severity` (repeatable), `min_severity`, `attack_class`; `order` = `severity` (default) \| `created_at` |
| `GET /runs/{run_id}/findings/upheld` | shorthand for `status=reach_upheld` |
| `GET /findings/{finding_id}` | full finding + `threat_model` + `provenance` + ordered `validation_trail` |
| `GET /findings/{finding_id}/validations` | per-pass rows (mechanical → bug → reachability) |
| `GET /findings?stable_key={key}` | cross-run history for one structural key (`dao.stable_key`), newest first |

`attack_class` is matched against the finding's `hunter_prompt_version`
(`<class>@<ver>`), the same convention the Hunt node and
`coverage.actionable_mechanical_failures` use.

### Recon & workspace artifacts

| Method & path | Notes |
|---|---|
| `GET /runs/{run_id}/artifacts` | index — `{path, kind, bytes, modified_at}` per file |
| `GET /runs/{run_id}/artifacts/{path}` | raw bytes; content-type by suffix; `403` traversal / symlink-escape, `404` missing, `413` over 25 MiB |
| `GET /runs/{run_id}/architecture` | `architecture.md` (`text/markdown`) |
| `GET /runs/{run_id}/recon/{name}` | `name` ∈ `seed` \| `module-map` \| `threat-model` \| `attack-surface` \| `task-manifest`; parsed JSON, `?raw=1` for bytes |
| `GET /runs/{run_id}/dedup/clusters` | `dedup/clusters.json` |
| `GET /runs/{run_id}/coverage-notes/{area}` | `coverage/<area>.md` (`text/plain`) |
| `GET /runs/{run_id}/log` | `run.log`; `?tail=N` for the last N lines |

### Execution state

| Method & path | Notes |
|---|---|
| `GET /runs/{run_id}/state` | latest LangGraph checkpoint — `recon_quality`, `subsystems`, `pending_hunts` (typed, `?limit=`), `pending_hunt_count`, `completed_cells`, `finding_ids`, `cycle_count` / `continuation_count` / `fork_count` / `token_spend`, `next_node`, `checkpoint_ts`. `404` if the run never checkpointed. |

### Wishlist (§9.3)

| Method & path | Notes |
|---|---|
| `GET /runs/{run_id}/wishes` | filter `status` (`open` \| `resolved` \| `requeued`) |
| `GET /wishes` | across all runs; defaults to `status=open`, newest first |
| `GET /wishes/{id}` | one wish |
| `POST /wishes/{id}/resolve` | body `{note?}`; sets `status=resolved`; **write token required** when auth is on. Does *not* itself re-queue the blocked task — that happens on the next `crucible run --resume <run_id>`. |

### Meta

`GET /health` → `{status, version, store_ok}` — always open, no auth.

---

## Triggering runs

The one endpoint that mutates anything, reaches the network, and spawns a
process. Everything else in this document is a `GET` against a database or the
filesystem; `POST /runs` fetches a **user-supplied git URL** and shells out to
`git clone`, then launches `crucible run`. Treat it accordingly.

```bash
curl -X POST "$API/runs" -H "Authorization: Bearer $CRUCIBLE_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"repo": "octocat/Hello-World", "ref": "main"}'
# -> 202 {"run_id": "4ae4e9098a19", "clone_status": "pending"}
# Location: /runs/4ae4e9098a19
```

- `repo` — `owner/repo` (resolved against `github.com`) or a full `https://` URL.
- `ref` — optional branch/tag/commit.
- Returns **`202`** immediately with a `run_id` — cloning and the run itself
  happen in the background. Poll `GET /runs/{run_id}` for `clone_status`
  (`pending → cloning → cloned` or `clone_failed` + `clone_error`), then treat
  it like any other run once `cloned`.
- **Write token required** (§ Auth above) — same token as `POST
  /wishes/{id}/resolve`. **This token can clone arbitrary public repos and
  spawn processes on this host — treat it like a deploy credential, not a
  read/write-findings toggle.**
- `422` — bad input: empty/malformed `repo`, a scheme other than `https://`, a
  host not on the allowlist, an IP-literal/loopback/link-local host, or a `ref`
  with shell-metacharacter-shaped content. Every one of these is rejected
  **before** any subprocess runs.
- `429` — too many runs already in progress (`CRUCIBLE_API_MAX_CONCURRENT_RUNS`).

### Env vars

| Var | Default | Meaning |
|---|---|---|
| `CRUCIBLE_API_RUNS_DIR` | `.crucible-runs` | where clones + per-run workspaces land: `<dir>/<run_id>/{repo,workspace}` |
| `CRUCIBLE_API_ALLOWED_GIT_HOSTS` | `github.com,gitlab.com,bitbucket.org` | the only hosts a clone may target. IP literals, loopback, and private/link-local ranges are hard-blocked regardless of this list. |
| `CRUCIBLE_API_MAX_CONCURRENT_RUNS` | `2` | runs with no `finished_at` allowed at once; a launch stuck cloning past `2 x` the timeout is swept and no longer counts |
| `CRUCIBLE_API_CLONE_TIMEOUT_S` | `120` | `git clone` timeout |
| `CRUCIBLE_API_CLONE_MAX_MB` | `500` | working-tree size cap, enforced after a shallow clone; over cap deletes the clone and fails the run |

### v1 limitations (by design)

- **Public repos only** — no credential/SSH-key handling for a private target
  repo yet.
- **No cancel** — once launched, a run finishes or fails on its own; there is
  no `DELETE`/kill endpoint.
- **No scheduling** — one-shot, triggered synchronously by the caller.

---

## Auth

No token configured → the API is **open**, and `crucible serve` refuses a
non-loopback `--host` unless you also pass `--no-auth`.

Set a token to require `Authorization: Bearer <token>` on every route except
`/health`:

```bash
export CRUCIBLE_API_TOKEN=$(openssl rand -hex 32)            # full: read + write
export CRUCIBLE_API_TOKEN_READONLY=$(openssl rand -hex 32)   # optional: GET only
crucible serve --host 0.0.0.0
```

- wrong / missing token → `401`
- readonly token on a write route (`POST /wishes/{id}/resolve`) → `403`
- if only `CRUCIBLE_API_TOKEN_READONLY` is set, that token is treated as full
- tokens are compared in constant time and never logged or returned by `/health`

---

## Deployment

### Behind a reverse proxy (TLS termination)

Run `uvicorn` on loopback and let the proxy handle TLS and rate limiting.

```nginx
server {
    listen 443 ssl;
    server_name crucible.internal.example;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        limit_req zone=crucible burst=20 nodelay;   # define limit_req_zone in http{}
    }
}
```

```bash
crucible serve --host 127.0.0.1 --port 8787
# uvicorn honours X-Forwarded-* from a trusted proxy:
#   uvicorn crucible.api.app:create_app --factory --proxy-headers --forwarded-allow-ips 127.0.0.1
```

Rate limiting is intentionally not in the app — do it at the proxy.

### Read-only against a live run

Point the API at the DB in read-only mode so a running `crucible run` keeps
exclusive write access:

```bash
crucible serve --store-url "sqlite:///findings.sqlite?mode=ro"
```

### CORS

Off by default. To allow a browser app on another origin:

```bash
export CRUCIBLE_API_CORS_ORIGINS="https://ui.internal.example,http://localhost:5173"
```

### Data residency

The process opens no outbound sockets. Everything it serves comes from
`findings.sqlite`, `checkpoints.sqlite`, and the workspace directory on the same
host.

---

## `curl` recipes

```bash
API=http://127.0.0.1:8787
# with auth:  H=(-H "Authorization: Bearer $CRUCIBLE_API_TOKEN")

# most recent run
RUN=$(curl -s "$API/runs?limit=1" | jq -r '.items[0].run_id')

# upheld findings, highest severity first
curl -s "$API/runs/$RUN/findings/upheld" | jq '.items[] | {severity, title, file_path}'

# one finding with its full validation trail
FID=$(curl -s "$API/runs/$RUN/findings?limit=1" | jq -r '.items[0].finding_id')
curl -s "$API/findings/$FID" | jq '{title, status, provenance, validation_trail}'

# the deterministic report
curl -s "$API/runs/$RUN/report" | jq '.counts'
curl -s "$API/runs/$RUN/report.md"

# has this bug been seen before, in any run?
KEY=$(curl -s "$API/findings/$FID" | jq -r '.stable_key')
curl -s "$API/findings?stable_key=$KEY" | jq '.items[] | {run_id, status, created_at}'

# coverage gaps
curl -s "$API/runs/$RUN/coverage" | jq '.gapfill_buckets'

# recon output
curl -s "$API/runs/$RUN/architecture"
curl -s "$API/runs/$RUN/recon/attack-surface" | jq '.[0:5]'

# what's blocking the harness right now
curl -s "$API/wishes" | jq '.items[] | {run_id, blocked_task_id, need}'
```
