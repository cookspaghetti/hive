import { useMemo, useState } from "react";
import { api } from "../api";
import { Chip, ErrorState, LoadingState, PageHeading, Surface, Timeline, titleCase } from "../components";
import { usePolling } from "../hooks";
import type { ActivityItem, JsonRecord } from "../types";

export function ActivityView({ logs = false }: { logs?: boolean }) {
  const [scope, setScope] = useState("all");
  const path = logs ? "/api/logs?limit=400" : "/api/activity?limit=300";
  const { data, error, loading, refresh } = usePolling(() => api<{items: ActivityItem[]}>(path).then(result => result.items || []), 5000, [path]);
  const categories = useMemo(() => ["all", ...new Set((data || []).map(item => logs ? String(item.level || "info") : item.category).filter(Boolean) as string[])], [data, logs]);
  const rows = (data || []).filter(item => scope === "all" || (logs ? String(item.level || "info") : item.category) === scope);
  if (loading && !data) return <LoadingState label={`Loading ${logs ? "logs" : "activity"}`}/>;
  if (error && !data) return <ErrorState message={error} retry={() => void refresh()}/>;
  return <div className="view"><PageHeading eyebrow={logs ? "Diagnostics" : "Audit trail"} title={logs ? "System logs" : "Activity ledger"} copy={logs ? "Runtime diagnostics for investigation and support. Credentials are redacted before display." : "A chronological, operator-readable record of important system actions."}/><Surface><div className="filter-row">{categories.map(item => <button key={item} className={scope === item ? "active" : ""} onClick={() => setScope(item)}>{titleCase(item)} <Chip>{item === "all" ? data?.length || 0 : data?.filter(row => (logs ? String(row.level || "info") : row.category) === item).length || 0}</Chip></button>)}</div>{logs ? <LogViewer rows={rows}/> : <Timeline items={rows}/>}</Surface></div>;
}

function LogViewer({ rows }: { rows: JsonRecord[] }) {
  if (!rows.length) return <div className="empty-state"><h3>No log lines</h3><p>The bounded log buffer does not contain entries for this level.</p></div>;
  return <div className="log-viewer"><header><span>Time</span><span>Level</span><span>Logger</span><span>Message</span></header>{rows.map((row,index) => <article key={String(row.id || index)}><time>{new Date(Number(row.ts || 0) * 1000).toLocaleTimeString()}</time><strong className={String(row.level || "info").toLowerCase()}>{String(row.level || "info").toUpperCase()}</strong><code>{String(row.logger || row.component || "hive")}</code><p>{String(row.message || row.detail || "")}</p></article>)}</div>;
}
