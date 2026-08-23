import { useState } from "react";
import { api, authenticatedUrl } from "../api";
import { Button, Chip, EmptyState, ErrorState, LoadingState, PageHeading, Surface, formatBytes, formatDate } from "../components";
import { usePolling } from "../hooks";
import type { EvidenceRow } from "../types";

export function VaultView() {
  const [selected, setSelected] = useState<EvidenceRow | null>(null);
  const { data, error, loading, refresh } = usePolling(() => api<EvidenceRow[]>("/api/evidence"), 10000, []);
  if (loading && !data) return <LoadingState label="Opening evidence vault"/>;
  if (error && !data) return <ErrorState message={error} retry={() => void refresh()}/>;
  return <div className="view"><PageHeading eyebrow="Immutable records" title="Evidence vault" copy="Signed case bundles, checksums, and packaged exports in one verification workspace."/>
    <Surface>{data?.length ? <div className="vault-layout"><div className="vault-list">{data.map(row => <button key={row.bundle_id || row.filename} className={selected === row ? "active" : ""} onClick={() => setSelected(row)}><span className="file-mark">ZIP</span><div><strong>{row.package_filename || row.filename || row.bundle_id}</strong><p>{formatDate(row.created_ts, true)} · {formatBytes(row.size)}</p></div><Chip tone={row.signature_present ? "success" : "warning"}>{row.signature_present ? "Signed" : "Unsigned"}</Chip></button>)}</div><aside className="vault-inspector">{selected ? <><p className="eyebrow">Bundle details</p><h2>{selected.package_filename || selected.filename}</h2><dl><dt>Created</dt><dd>{formatDate(selected.created_ts, true)}</dd><dt>Size</dt><dd>{formatBytes(selected.size)}</dd><dt>SHA-256</dt><dd><code>{selected.sha256 || "Not reported"}</code></dd><dt>Signature</dt><dd>{selected.signature_present ? "Present" : "Not present"}</dd></dl><div className="button-stack">{selected.package_download_url && <Button tone="primary" onClick={() => window.open(authenticatedUrl(selected.package_download_url!), "_blank", "noopener")}>Download package</Button>}{selected.download_url && <Button onClick={() => window.open(authenticatedUrl(selected.download_url!), "_blank", "noopener")}>Download evidence</Button>}</div></> : <EmptyState title="Select a bundle" copy="Choose a sealed record to inspect its integrity metadata."/>}</aside></div> : <EmptyState title="Vault is empty" copy="Evidence bundles appear here after a live case or demo is sealed."/>}</Surface>
  </div>;
}
