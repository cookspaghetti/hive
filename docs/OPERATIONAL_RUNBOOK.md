# HIVE Operational Runbook

Status: prototype operating procedure, 24 August 2026

## Safety boundary

Operate HIVE only with authorised research accounts and synthetic or consented
data. The commands in this runbook do not send Telegram messages unless an
operator deliberately starts or resumes a takeover. HIVE has no automatic
evidence deletion. Do not use `docker compose down -v`, remove
`qdrant_storage/`, or remove `evidence/` as a reset procedure.

## Standard start and acceptance check

From the repository root:

```powershell
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker compose ps
task services:verify
task models:verify
task sandbox:verify
```

Accept the deployment only when all four services are running and healthy and
all three verifiers return `"ok": true`. The checks cover:

- PostgreSQL write/read/delete and Qdrant index/search/delete round trips;
- visible text generation by every distinct configured cheap, light, strong,
  and vision model role;
- a public URL fetch, private-address rejection, and sandbox-container cleanup.

The model verifier uses a minimal synthetic prompt and consumes a small number
of provider tokens. The sandbox verifier visits only `https://example.com` by
default and writes disposable output below `/tmp` inside the backend.

After acceptance, open `http://127.0.0.1:9130/` and confirm the runtime page
shows the LLM, Qdrant, case intelligence, engine, Telegram userbot, and control
bot as ready. Startup now performs a real cheap-tier generation check. A model
that remains in the provider's loading state is retried three times and then
reported as an error instead of being accepted as an empty reply.

## Routine pre-demonstration checklist

1. Record the Git commit with `git rev-parse --short HEAD`.
2. Confirm `docker compose ps` shows four healthy services.
3. Run the three acceptance verifiers above.
4. Confirm there are no unexpected active or paused takeovers in the panel.
5. Confirm the Retention page has no unexplained unavailable store or sharp
   count increase.
6. Use a new synthetic Demo Lab run. Do not reuse a participant chat for a
   rehearsal.
7. Confirm the selected demo mode, scenario, delay, and recording indicator
   before pressing Start.
8. Keep the panel on loopback. Do not publish backend, PostgreSQL, or Qdrant
   ports.

## Safe restart and takeover recovery

Use the panel runtime controls for a normal stop or restart. HIVE refuses a
normal restart while a takeover is active. Let the current exchange finish and
stop the takeover first.

Use a forced restart only for a supervised recovery test or incident. Before
doing so, record the affected case/session and reason. An interrupted takeover
checkpoint returns as `paused_after_restart`; HIVE must not automatically send
a reply. Review the recovered transcript and then explicitly choose Resume or
Abandon. Abandon removes only that unfinished checkpoint, not sealed evidence,
history, audit records, or case intelligence.

After any restart:

```powershell
docker compose ps
task services:verify
task models:verify
```

Then confirm the panel's active and recovery-paused counts match the operator's
notes. Duplicate outbound messages, missing checkpoint turns, or invalid audit
state are stop conditions for the test.

## Incident triage

### Model verifier or startup LLM probe fails

Run `task models:verify` and retain its role/model, latency, token-count, and
error output. Check the configured base URL, API-key status, and advertised
model aliases without printing the key. A transient provider loading response
is retried automatically. Do not proceed with live takeovers if any required
role remains unavailable; model fallback is a behaviour change and must be
recorded and re-evaluated before use.

### PostgreSQL or Qdrant fails

Inspect service health and recent logs:

```powershell
docker compose ps
docker compose logs --tail 200 postgres
docker compose logs --tail 200 qdrant
docker compose logs --tail 200 backend
task services:verify
```

HIVE treats PostgreSQL as authoritative for exact case relationships and
Qdrant as a redacted semantic candidate index. Do not manually write Qdrant
points to repair drift. After PostgreSQL is verified, use the documented
reindex procedure in `docs/CASE_VECTOR_REINDEX.md`.

### Forensic sandbox fails

Run `task sandbox:verify`. On Docker Desktop the inner sandbox deliberately
inherits the outer backend's 4 GiB and PID limits; per-child cgroup flags are
disabled because nested cgroup v2 rejects them. The sandbox still runs as a
read-only, capability-dropped, no-new-privileges disposable container.

Inventory leftovers without deleting them first:

```powershell
docker compose exec -T backend docker ps -a --filter name=hive-sandbox-
```

An unexpected leftover is an incident. Record its name and logs before a
targeted `docker rm -f <exact-container-name>`. Never use a broad container-name
pattern for deletion.

### Panel is unavailable

Confirm the frontend and backend health, then inspect their recent logs. The
frontend is the only published service and must remain bound to
`127.0.0.1:9130`. Do not expose the backend directly to work around a proxy or
token problem.

## Evidence and audit integrity

Verify an exported evidence package before transfer:

```powershell
uv run hive-verify <path-to-package>
```

Create and test an immutable audit-ledger snapshot only when the study requires
that checkpoint:

```powershell
task audit:backup
task audit:restore-drill
```

The restore drill is isolated and does not replace live data. Follow
`docs/AUDIT_BACKUP_RESTORE.md`. Whole-system backup remains deferred until the
core FYP objectives are complete; this must not be represented as full disaster
recovery coverage.

Signing-key rotation is performed from Setup > Security while the runtime is
stopped. Record the old fingerprint, new fingerprint, operator, reason, and
time. Retain the old key under the approved custody plan so previously signed
packages remain independently verifiable. Never display or copy private key
material into UAT evidence.

## Non-destructive evaluation reset

There is intentionally no global reset button. For another rehearsal or UAT
round:

1. stop or finish the current synthetic demo;
2. create a new Demo Lab run and record its run ID, build, scenario, and mode;
3. store new performance/UAT results under a newly named result set;
4. filter or compare by run ID instead of deleting older runs; and
5. use the Retention inventory to flag old synthetic runs for later reviewed
   disposal.

Do not truncate PostgreSQL tables, delete Qdrant collections, clear audit JSONL,
or remove evidence directories to obtain a clean screen. A future destructive
reset must be narrowly scoped to synthetic run IDs, backup-aware, audited, and
separately approved before implementation.

## Release and image update procedure

PostgreSQL and Qdrant are pinned by version and digest in `docker-compose.yml`.
For an intentional upgrade:

1. read the vendor release and migration notes;
2. record the old and proposed version/digest;
3. run the full deterministic test gate;
4. recreate one local evaluation stack without removing volumes;
5. run all acceptance verifiers and the relevant UAT regression cases; and
6. commit the pin change separately from application behaviour changes.

Never replace a stateful image pin with `latest`. Local HIVE application image
tags may remain `latest` because they are rebuilt from the recorded Git commit.

## Shutdown

Confirm no takeover is active, stop the runtime from the panel, then run:

```powershell
docker compose down
```

This preserves named volumes and bind-mounted evidence. Do not add `-v` unless
a separately approved destructive disposal procedure names and verifies every
target.

## Evidence to retain for operational UAT

For each formal run, retain the Git commit, Compose service/image status,
verifier JSON, runtime component status, selected demo/test IDs, timestamps,
tester identity, pass/fail result, observed latency, and any incident notes.
Use `docs/HIVE_UAT_AND_PERFORMANCE_DRAFT.md` as the acceptance and measurement
worksheet.
