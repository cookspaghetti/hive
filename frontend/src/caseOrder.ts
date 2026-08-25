import type { JsonRecord } from "./types";

export function caseActivityTimestamp(row: JsonRecord): number {
  const candidates = [
    row.last_message_at,
    row.last_message_ts,
    row.ended_ts,
    row.started_ts,
    row.created_ts,
  ];
  for (const candidate of candidates) {
    const timestamp = Number(candidate);
    if (Number.isFinite(timestamp) && timestamp > 0) return timestamp;
  }
  return 0;
}

export function compareCasesNewestFirst(a: JsonRecord, b: JsonRecord): number {
  const timestampDifference = caseActivityTimestamp(b) - caseActivityTimestamp(a);
  if (timestampDifference) return timestampDifference;
  return caseIdentity(b).localeCompare(caseIdentity(a), undefined, { numeric: true });
}

function caseIdentity(row: JsonRecord): string {
  return String(row.history_id ?? row.id ?? row.peer_id ?? "");
}
