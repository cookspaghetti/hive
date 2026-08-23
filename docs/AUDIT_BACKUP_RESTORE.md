# Audit Backup and Restore Drill

This procedure verifies that a HIVE audit-ledger backup is readable and
internally consistent without replacing the authoritative ledger or modifying
PostgreSQL, Qdrant, evidence bundles, or any live service.

## Create a fresh backup

```powershell
task audit:backup
```

The command refuses a source ledger whose hash chain is invalid. It writes a
uniquely named JSONL snapshot, its source-identity sidecar, and a manifest under
`evidence/audit/backups/`. The manifest records the event count, source ID,
terminal event hash, and SHA-256 checksum.

## Run the isolated restore drill

```powershell
task audit:restore-drill
```

By default, HIVE selects the newest backup manifest, creates a uniquely named
directory in the operating system's temporary area, copies the backup and
source identity into it, and performs these checks:

1. supported manifest schema and a path-confined JSONL filename;
2. backup SHA-256 against the manifest;
3. source-identity sidecar against the manifest;
4. complete JSONL parsing and hash-chain verification;
5. restored event count and terminal hash against the manifest; and
6. restored ledger identity against the manifest.

The restored files and their newly created directory are removed after a pass.
A structured result remains in `evaluation/results/backup_restore/`. It states
that the source and live stores were not modified.

For an explicit manifest or controlled work directory:

```powershell
task audit:restore-drill -- --manifest evidence/audit/backups/<manifest>.json --work-root evidence/audit/restore_work
```

Use `--keep-restored` only when an operator needs to inspect the isolated copy.
The command never accepts an existing restore directory and never overwrites a
ledger. The operator is responsible for removing a deliberately retained copy.

## Recording the drill

Retain the backup manifest and the generated result JSON. Record the build,
operator, time, event count, all check results, duration, and report path in the
UAT evidence log. A pass demonstrates recoverability of the authoritative local
audit stream; it does not demonstrate a complete disaster recovery of every
HIVE data store.

## Current recovery boundary

This drill covers the authoritative JSONL audit ledger. PostgreSQL is a
query-oriented mirror for audit records, while PostgreSQL case/history data,
Qdrant vectors, filesystem evidence/media, secrets, and model caches have
separate custody requirements. A future whole-system recovery plan still needs
coordinated database dumps, Qdrant snapshots, evidence-file manifests, encrypted
secret escrow decisions, recovery ordering, and stated recovery-point and
recovery-time objectives.

