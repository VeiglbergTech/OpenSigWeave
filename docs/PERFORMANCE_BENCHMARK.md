# Engine throughput and concurrency

This document describes the **HTTP signature engine**. A successful request
selects a signature, resolves identity attributes, sanitizes and renders HTML,
and returns a nonempty signature payload. It does **not** measure complete SMTP
throughput, MIME rewriting by Rspamd/Postfix/Stalwart, queues, DKIM or delivery.

## Prior incident: what is established

The original dev benchmark (November 22, 2026) observed:

| Scenario | Concurrent | Success | Valid signatures/s |
|---|---:|---:|---:|
| Fixed signature | 10 | 200/200 | 100.72 |
| Fixed signature | 50 | 2/200 | 0.03 |
| Rotating group | 10 | 200/200 | 67.68 |
| Rotating group | 50 | 9/200 | 0.15 |

These are **historical measurements**, not new results or promised throughput.
Before this fix, the async FastAPI engine used synchronous SQLAlchemy queries
and rendering directly on the event loop. The group rotation additionally used
`FOR UPDATE` on shared signature/member catalog rows, serializing independent
principals. Both are demonstrable from the code. The percentages of the failure
attributable to the ASGI loop, DB locks and the old urllib/thread-per-connection
benchmark client were **not independently quantified** by profiling. The old
client conflated network errors and timeouts.

## Changes and correctness properties

- Shared engine resolution is offloaded to a bounded AnyIO worker thread; the
  synchronous database session is not used concurrently. Async identity
  providers continue running within that worker's private event loop. DB,
  filesystem and sanitizing operations no longer block the ASGI event loop.
  Non-engine admin routes have **not** been generally converted to async DB I/O.
- A transaction takes an exclusive lock on `group_selection_state` for the
  immutable `(principal_id, group_id)` key. Eligible members are read after
  the lock, without locking shared `signatures` or group-member catalog rows.
  The selection and state update still roll back together if rendering or MIME
  modification fails. Different principals can progress independently.
- Candidate weighting, no-repeat (when there are 2+ eligible members), rule
  priorities, schedule precedence, authentication and HTML sanitization are
  unchanged.
- PostgreSQL SQLAlchemy pool parameters and Uvicorn workers are configurable.
  All pool limits are **per process**, and database connections increase with
  worker count. Workers use the same PostgreSQL rotation state.
- An async HTTP load generator uses a bounded keep-alive connection pool and
  semaphore. It reports HTTP statuses and distinguishes connect/read/write/
  connection-pool timeouts. Missing or malformed signature HTML counts as a
  failure. `--fail-on-errors` makes the workflow fail, after writing results.

**Important:** Offloading to a thread does not make synchronous SQLAlchemy
operations nonblocking inside that thread. Lock waits, provider latency and
large HTML rendering can still limit throughput. Holding the rotation row
lock until the final commit is deliberate: committing early could consume a
rotation even if a later step fails.

## Deployment configuration

| Environment variable | Default | Valid range | Notes |
|---|---:|---:|---|
| `UVICORN_WORKERS` | 1 | positive Uvicorn worker count | Set only after validating startup and database capacity |
| `DB_POOL_SIZE` | 5 | 1–50 | PostgreSQL persistent connections per worker |
| `DB_MAX_OVERFLOW` | 10 | 0–50 | Extra transient PostgreSQL connections per worker |
| `DB_POOL_TIMEOUT` | 10 | 1–120 | Seconds to wait for an available connection |
| `DB_POOL_RECYCLE` | 1800 | 60–86400 | Seconds between recycled pool connections |
| `DB_SELECTION_LOCK_TIMEOUT_MS` | 8000 | 100–60000 | PostgreSQL rotation row-lock wait bound per transaction |

Examples (starting points **not** benchmark-verified recommendations):

- Small instance: one Uvicorn worker, pool 5 + overflow 10, default timeouts.
- Larger instance: two workers, pool 10 + overflow 5 each, provided at least 30
  application DB connections plus maintenance headroom are actually available.
- Do not set Uvicorn workers to 4 and a pool of 50 + overflow 50 on a PostgreSQL
  server with `max_connections=100`: the theoretical application demand would
  be 400. Account for other clients, backups and admin sessions.

Changes to worker count require a container recreation after setting `.env`.
Migration occurs before Uvicorn spawns workers. Consider provider-specific
connection limits and available CPU/RAM when increasing workers. A multi-worker
configuration is only suitable after regression testing the complete mail flow.

## Reproducible isolated benchmark

Never run a load test against a real mail flow. The workflow
`.github/workflows/benchmark.yml` builds a **throwaway PostgreSQL volume**,
seeds only reserved `.test` domains, uses fake identity attributes (cached OIDC),
and sends no SMTP messages. It is triggered by `dev` pushes. The quick profile
runs 500 requests/stage, stress runs 1000, with all levels
`1,5,10,25,50,100,250,500`, 15s per-request timeout.

Four workloads: fixed signature / 128 senders, weighted rotation / 128 senders,
rotation / one sender, and a large (32 KB) fixed HTML template / 128 senders.
The single-sender case tests intentional contention on its rotation state,
not independent-sender scalability.

On a clean, isolated Docker host with a dedicated `.env` containing generated
secrets, `APP_ALLOWED_HOSTS=localhost,127.0.0.1`, and a **new empty** PostgreSQL
volume:

```bash
docker compose up -d --build --wait
docker compose exec -T -e BENCHMARK_DISPOSABLE_DB=1 opensigweave \
  python -m scripts.seed_signature_benchmark --confirm-disposable --senders 512
# Load credentials solely from this disposable stack's local .env.
set -a
source .env
set +a
python scripts/benchmark_engine.py --domain bench-static.example.test \
  --levels 1,5,10,25,50,100,250,500 \
  --requests-per-level 500 --sender-count 128 --warmup 10 \
  --fail-on-errors --output benchmark-results/static.json
python scripts/benchmark_engine.py --domain bench-rotation.example.test \
  --levels 1,5,10,25,50,100,250,500 \
  --requests-per-level 500 --sender-count 1 --warmup 10 \
  --fail-on-errors --output benchmark-results/rotation-one.json
```

Do not use an existing volume: the seeder refuses a populated application DB,
but cannot prove a database is genuinely disposable. Remote origins require
explicit HTTPS and `--allow-remote`; redirects are not followed. Test
authentication and authorization stay enabled; requests require the engine key.

The GitHub runner also records `host.txt` and periodic JSONL samples of
`docker stats`, PostgreSQL connection states, lock waiters and configured
Uvicorn worker/pool limits. A sample is not a CPU/RAM peak; missing telemetry
must be reported as missing. Evaluate 429, 5xx, invalid payloads, timeout
subtypes and client-side errors separately. P50/P95/P99 describe **successful**
requests, so always read them alongside failure counts and total throughput.
Do not interpret a green workflow, zero observed lock waiters in sampled
instants or a high successful-requests/s number alone as proof of correctness.

## Reading capacity and before/after data

A concurrency level is reliable only when **all** requests at that level
return the expected valid nonempty signature, zero timeouts/other errors, and
the rotation invariants pass. The highest reliable level is the maximum
observed contiguous successful level; missing samples cannot count as success.

For apples-to-apples before/after results, run both code revisions with the
same seeded database state, same HTTP client, workload mix, request count,
timeout, CPU allocation, worker/pool settings and host. Shared GitHub runners
vary; prefer repeating on a pinned dedicated runner. The historic numbers
above used a different HTTP client and cannot be quantitatively compared
with the upgraded async client without repeating the old revision under
matching conditions. Record both revisions, CPU, RAM, PostgreSQL waiters,
worker count and results; do not fill in unmeasured cells.

## Known limits and investigations not yet closed

- Same-principal messages must serialize when selecting from the same group
  to preserve no-repeat and transactional rollback. This is intentional.
- Provider cache-miss performance requires a controlled test provider or LDAPS
  fixture. The automated benchmark currently measures **OIDC cache hits** and
  does not make a claim about live identity-provider throughput.
- Sampling does not capture microsecond-scale lock waits or continuous peaks.
- Benchmark output measures HTTP engine computations only; SMTP throughput
  requires an independent end-to-end mail transport test with MIME and DKIM
  validation. No mail messages are emitted by this benchmark.
