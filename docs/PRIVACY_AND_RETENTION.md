# HIVE Privacy and Retention Policy

Status: report-only prototype policy, 23 August 2026

## Purpose and safety boundary

HIVE inventories locally stored artifacts and flags items whose age exceeds an
operator-defined review threshold. The current release does **not** delete,
expire, overwrite, or anonymise any artifact automatically. A review-due label
means that a responsible operator must decide what to do under the approved FYP
ethics and data-management plan; it is not an instruction to delete evidence.

The panel exposes this inventory under **Retention**. Saving a policy changes
only the review thresholds in `.env`, records a hash-chained audit event, and
rescans the inventory.

## Artifact classes and default review policy

| Artifact class                                                          |               Default | Current treatment                                                 |
| ----------------------------------------------------------------------- | --------------------: | ----------------------------------------------------------------- |
| Signed evidence, authoritative case profiles, history and analysis runs |            Indefinite | Protected; no expiry calculation or deletion                      |
| Captured Telegram media                                                 |  Review after 30 days | Flag only                                                         |
| Synthetic Demo Lab runs                                                 |  Review after 90 days | Flag only                                                         |
| Evaluation results                                                      | Review after 180 days | Flag only                                                         |
| Hash-linked audit ledger and backups                                    |            Indefinite | Protected to preserve audit continuity                            |
| Unfinished takeover checkpoints                                         |   Review after 7 days | Protected; stale state is flagged for recovery/abandonment review |

Thresholds may be set from 1 to 3,650 days. Signed evidence and audit records
cannot be converted to expiring classes through this interface.

## Data locations and movement

- Private-chat text and approved media enter the local HIVE runtime through
  Telegram. The operator must use only authorised, consented research accounts.
- Exact transcripts, validated indicators, provenance, and signed packages are
  stored on the local filesystem and/or in PostgreSQL.
- Conversation text may be sent to the configured cloud-compatible text-model
  endpoint. Do not use participant or third-party data unless the approved
  information sheet and consent/data-processing terms cover that endpoint.
- Images use local QR decoding and OCR first. When local structured extraction
  is insufficient, the configured vision fallback may receive image content.
- Qdrant receives identifier-redacted scam-pattern representations for
  cross-case candidate retrieval. PostgreSQL—not Qdrant—remains authoritative
  for exact identifiers and relationships.
- The URL sandbox visits approved public targets inside a disposable container;
  private and loopback targets are rejected.

## Access, review and handling procedure

1. Restrict the panel to loopback access and protect its API with the HIVE panel
   token. Restrict host, `.env`, `secrets/`, database volumes, and `evidence/` to
   the named researcher/operator.
2. Before participant testing, record the build, dataset owner, lawful/ethical
   basis, approved purpose, review thresholds, model endpoints, and final
   disposal date in the study data-management record.
3. Use the Retention page to record file/record counts and review-due items.
   Investigate unexpected counts or unavailable stores before accepting a scan.
4. Never remove an active checkpoint until the operator has resumed or
   explicitly abandoned the recovery through the takeover workflow.
5. Preserve signed evidence and audit continuity for the period approved by the
   supervisor. Any later disposal must use a separately reviewed, logged,
   backup-aware procedure; HIVE does not yet implement that procedure.
6. Export only the minimum approved material. Verify portable evidence packages
   before transfer and keep participant identity/consent records separate from
   pseudonymised test results.

## Known limitations and required decision before formal UAT

- Storage measurement includes filesystem sizes and PostgreSQL relation sizes;
  Qdrant reports point counts but not physical collection bytes through the
  current inventory adapter.
- A scan is a point-in-time inventory and does not lock concurrent writes.
- HIVE has no secure-deletion, legal-hold, per-case export, whole-system restore,
  or automatic enforcement workflow. The audit-ledger restore drill is isolated
  and does not imply recovery coverage for every store.
- The supervisor/ethics process must approve the final retention duration,
  participant disclosure, cloud-model processing, backup handling, and disposal
  method before formal UAT data is collected.
