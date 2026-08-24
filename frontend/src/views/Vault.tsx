import { useEffect, useState } from "react";
import { api, authenticatedUrl } from "../api";
import { Button, Chip, EmptyState, ErrorState, LoadingState, PageHeading, Surface, formatBytes, formatDate, titleCase } from "../components";
import { usePolling } from "../hooks";
import type { EvidenceRow, JsonRecord } from "../types";

export function VaultView() {
  const [selected, setSelected] = useState<EvidenceRow | null>(null);
  const bundles = usePolling(() => api<EvidenceRow[]>("/api/evidence"), 10_000, []);
  const audit = usePolling(() => api<JsonRecord>("/api/audit/status").catch(() => ({})), 10_000, []);
  useEffect(() => {
    const first = bundles.data?.[0];
    if (!selected && first) setSelected(first);
  }, [bundles.data, selected]);
  if (bundles.loading && !bundles.data) return <LoadingState label="Opening evidence vault" />;
  if (bundles.error && !bundles.data) return <ErrorState message={bundles.error} retry={() => void bundles.refresh()} />;
  const status: JsonRecord = audit.data || {};
  return <div className="view">
    <PageHeading eyebrow="Immutable records" title="Evidence vault" copy="Signed case bundles, verification metadata, and chain-of-custody in one workspace." actions={<Chip tone={status.valid === false ? "danger" : "success"}>Hash chain {status.valid === false ? "failed" : "intact"} · {String(status.events ?? "—")} records</Chip>} />
    <Surface>{bundles.data?.length ? <div className="vault-layout">
      <div className="vault-table">
        <header><span>Peer</span><span>Sealed</span><span>Size</span><span>SHA-256</span><span>Package</span></header>
        {bundles.data.map(row => <button key={row.bundle_id || row.filename} className={(selected?.bundle_id || selected?.filename) === (row.bundle_id || row.filename) ? "active" : ""} onClick={() => setSelected(row)}>
          <code>{row.peer_id || "—"}</code><time>{formatDate(row.created_ts, true)}</time><span>{formatBytes((row.package_size as number) || row.size)}</span><code>{row.sha256 ? `${row.sha256.slice(0, 12)}…` : "—"}</code><Chip tone={row.package_present && row.signature_present ? "success" : "warning"}>{row.package_present && row.signature_present ? "Verified files" : "Review"}</Chip>
        </button>)}
      </div>
      <Inspector row={selected} />
    </div> : <EmptyState title="Vault is empty" copy="Evidence bundles appear here only after rendering, signing, and archival all succeed." />}</Surface>
  </div>;
}

function Inspector({ row }: { row: EvidenceRow | null }) {
  const [metadata, setMetadata] = useState<JsonRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setMetadata(null); setError("");
    if (!row?.metadata_url) return;
    setLoading(true);
    void api<JsonRecord>(row.metadata_url).then(value => { if (active) setMetadata(value); }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [row?.metadata_url]);
  if (!row) return <aside className="vault-inspector"><EmptyState title="Select a bundle" copy="Choose a sealed record to inspect its integrity metadata." /></aside>;
  const verification = (metadata?.verification || {}) as JsonRecord;
  const checks = (verification.checks || {}) as Record<string, boolean>;
  const checksumKeys = ["pdf_checksum", "signature_checksum", "public_key_checksum"];
  const checksumPass = checksumKeys.some(key => typeof checks[key] === "boolean") ? checksumKeys.every(key => checks[key] === true) : undefined;
  const custody = Array.isArray(metadata?.custody) ? metadata.custody as JsonRecord[] : [];
  const fingerprint = String(((verification.manifest || {}) as JsonRecord).signing_key_fingerprint || "Not reported");
  const verificationRows = [
    ["Safe package structure", checks.safe_structure],
    ["File checksums", checksumPass],
    ["Manifest signature", checks.manifest_signature],
    ["PDF signature", checks.pdf_signature],
    ["Audit hash chain", checks.hash_chain],
  ] as Array<[string, boolean | undefined]>;
  return <aside className="vault-inspector">
    <p className="eyebrow">Bundle details</p><h2>{row.package_filename || row.filename}</h2>
    <p>{metadata ? `Sealed by ${String(metadata.operator_name || "an unrecorded operator")} on ${formatDate(Number(metadata.sealed_ts || row.created_ts), true)}.` : "Loading the signed manifest and custody ledger."}</p>
    <div className="button-stack">{row.package_download_url && <Button tone="primary" onClick={() => open(row.package_download_url)}>Download ZIP</Button>}{row.download_url && <Button onClick={() => open(row.download_url)}>Download PDF</Button>}</div>
    <dl><dt>Peer</dt><dd><code>{row.peer_id || "—"}</code></dd><dt>Size</dt><dd>{formatBytes(row.size)}</dd><dt>SHA-256</dt><dd><code>{row.sha256 || "Not reported"}</code></dd><dt>Signing key</dt><dd><code>{fingerprint}</code></dd></dl>
    <h3>Verification</h3>
    {loading ? <LoadingState label="Verifying package" /> : error ? <ErrorState message={error} /> : <div className="verification-list">{verificationRows.map(([label, pass]) => <article key={label}><span className={`status-dot ${pass === true ? "success" : pass === false ? "error" : "info"}`} /><strong>{label}</strong><span>{pass === true ? "Passed" : pass === false ? "Failed" : "Unavailable"}</span></article>)}</div>}
    {Array.isArray(verification.errors) && verification.errors.length > 0 && <div className="verification-errors"><strong>Verification needs attention</strong>{verification.errors.map((item, index) => <p key={index}>{String(item)}</p>)}</div>}
    <h3>Chain of custody</h3>
    {custody.length ? <div className="custody">{custody.map((item, index) => <article key={`${String(item.ts)}-${index}`}><time>{formatDate(Number(item.ts), true)}</time><span /><div><strong>{titleCase(String(item.title || item.event || item.action || "Recorded event"))}</strong><p>{String(item.detail || item.summary || "Tamper-evident activity recorded.")}</p>{Boolean(item.hash) && <code>{String(item.hash).slice(0, 24)}…</code>}</div></article>)}</div> : <p className="muted-copy">No case-scoped custody events were linked to this legacy bundle.</p>}
    <p className="claim-limit">A cryptographic pass proves these bytes are unchanged since sealing; it says nothing about sender identity.</p>
  </aside>;
}

function open(path?: string) { if (path) window.open(authenticatedUrl(path), "_blank", "noopener"); }
