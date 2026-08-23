# Case-vector compatibility and reindex procedure

## Purpose

PostgreSQL is HIVE's authoritative store for sealed case profiles and exact
identifier relationships. Qdrant's `hive_cases` collection is a derived,
privacy-reduced candidate index and may be rebuilt without changing source
evidence.

Embedding vectors are comparable only when their generation settings match.
HIVE therefore fingerprints the following inputs:

- embedding-schema version;
- FastEmbed provider version;
- model identifier;
- pooling behaviour;
- query/document prefix policy;
- vector dimension and cosine distance.

The full SHA-256 fingerprint is stored in collection metadata and in every
point payload. Search and readiness probes filter on the configured
fingerprint. An unversioned or incompatible collection fails semantic
readiness with a reindex instruction instead of silently mixing vector spaces.

FastEmbed is pinned to `0.8.0`. For the configured multilingual MiniLM model,
HIVE intentionally uses FastEmbed's mean-pooling behaviour and records that
choice in the descriptor. The targeted upstream warning is suppressed only
after this compatibility control is applied.

## Safe reindex

Run a read-only preview first:

```powershell
docker compose run --rm --no-deps --entrypoint /app/.venv/bin/python backend -m hive.case_reindex --dry-run
```

Then rebuild from PostgreSQL:

```powershell
docker compose run --rm --no-deps --entrypoint /app/.venv/bin/python backend -m hive.case_reindex
```

If the descriptor or fingerprint changed, stop the backend before the reindex
and restart it only after verification. This prevents an older running process
from querying while the migration is in progress. A same-fingerprint repair is
safe online because every query filters the fingerprint.

The command performs these checks in order:

1. Load all authoritative PostgreSQL case profiles.
2. Refuse an empty source by default, preventing accidental index erasure.
3. Upsert every eligible non-benign profile with the current fingerprint.
4. Remove points that no longer have an eligible authoritative case.
5. Verify that Qdrant case IDs and compatible fingerprints exactly match the
   eligible PostgreSQL set.
6. Update collection metadata only after complete verification.
7. Append start, completion, or failure events to the permanent audit ledger.

Upserts are deterministic by case ID and the procedure is safe to rerun. If a
run is interrupted, fingerprint-filtered retrieval ignores any older points;
rerunning restores complete coverage. Use `--allow-empty` only for an approved
intentional purge of this derived index.

Afterward, run the privacy and schema verifier:

```powershell
Get-Content -Raw scripts/verify_case_vector_privacy.py | docker compose exec -T backend python -
```

Expected results are zero exact-identifier leaks, zero payload-key/schema/
embedding violations, matching collection metadata, and `valid: true`.

## Dependency upgrade rule

Do not change FastEmbed, the embedding model, pooling, prefix policy, dimension,
or distance metric in a deployed build without:

1. updating the controlled dependency lock;
2. reviewing the resulting descriptor and fingerprint;
3. running this reindex;
4. rerunning similarity quality/performance measurements; and
5. recording the evaluated commit and dependency/model versions.

Changing vector dimension requires a separately approved collection-rebuild
migration; the current command fails closed rather than deleting the
collection automatically.
