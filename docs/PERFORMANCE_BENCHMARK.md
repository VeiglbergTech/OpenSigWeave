# OpenSigWeave engine performance benchmark

This benchmark measures **real HTTP signature selection and rendering**, not
complete SMTP email signing/injection. It includes PostgreSQL queries, cached
identity attributes, schedule evaluation, file-backed templates, sanitization,
template rendering and the HTTP response. It does NOT include Rspamd/Milter
MIME changes, DKIM signing, SMTP queues, transport or delivery.

## Automated runs

The Engine performance benchmark workflow runs the quick profile on dev pushes
(10 and 50 concurrent requests, 200 requests per stage). A manually dispatched
stress profile covers 10, 50, 100 and 500 concurrent requests with 1000
requests per stage. Workflow dispatch is only available after the workflow
exists on GitHub's default branch; dev pushes still trigger the quick profile.

The workflow creates **a disposable Docker Compose stack and fresh PostgreSQL
volume**, seeds real signature files and metadata, and uses a reserved
example.test domain. It benchmarks (1) a scheduled fixed signature and (2)
a scheduled weighted no-repeat group of three signatures. Each fixture
contains 512 sender identities with cached OIDC claims so an external
identity provider does not dominate the result. No production mail is sent.

Results are archived as JSON and shown in the GitHub Actions summary:
successes, failures and error types, successful requests per second, P50/P95/P99,
mean and maximum HTTP latency, concurrency and visible CPU count.
A request only counts as successful if it returns non-empty HTML containing
BENCHMARK_SIGNATURE. Measured errors and timeouts are included as explicit warnings while all stages
continue to run.
The application container is restarted before each separate concurrency stage;
this prevents the 50-request stress result from leaving the single Uvicorn worker
unresponsive and invalidating the next case. PostgreSQL fixtures and rotation
state are retained across stages. A successful workflow does not mean every request succeeded;
read the measurements. For an explicit strict zero-error run, pass
--fail-on-errors to the client. Setup/configuration errors still fail CI.
This independent benchmark is not a mandatory dev-to-beta release gate:
shared GitHub runners have variable CPU and I/O characteristics.

## Running against an isolated local test stack

Create a new disposable .env based on .env.example, with
APP_ALLOWED_HOSTS=localhost,127.0.0.1 and dedicated generated secrets.
NEVER use an existing production database or Compose volume. The seeder
requires an explicit opt-in and refuses pre-existing application domains,
signatures or attribute cache records.

~~~bash
docker compose up -d --build --wait
docker compose exec -T -e BENCHMARK_DISPOSABLE_DB=1 opensigweave \
  python -m scripts.seed_signature_benchmark --confirm-disposable --senders 512
read -r -s -p "Disposable engine key: " ENGINE_API_KEY; echo
export ENGINE_API_KEY
python scripts/benchmark_engine.py --domain bench-static.example.test \
  --levels 10,50,100,500 --requests-per-level 1000 --sender-count 128 \
  --output benchmark-results/static.json
python scripts/benchmark_engine.py --domain bench-rotation.example.test \
  --levels 10,50,100,500 --requests-per-level 1000 --sender-count 128 \
  --output benchmark-results/rotation.json
~~~

Set --sender-count 1 to measure a single high-traffic sender and potential
per-identity database contention. Compare with many distinct senders to reveal
global contention from row locks. For an explicitly authorized remote staging
deployment, --base-url must use HTTPS and --allow-remote is required. The
engine key comes only from ENGINE_API_KEY in the environment, and redirects
are not followed.

## Interpreting the numbers

Report the test CPU, Uvicorn worker count, PostgreSQL topology and signature
complexity. Parallel requests are *not* emails per second. For a credible SMTP
emails-per-second claim, a separate staging test must send real authenticated
MIME messages through the complete adapter and mail-server chain, check
delivered content and DKIM, and record queueing, retries, errors and resources.
Never load-test a production SMTP server from GitHub-hosted CI.
