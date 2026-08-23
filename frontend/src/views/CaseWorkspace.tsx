import { useMemo, useState } from "react";
import { api, authenticatedUrl, post } from "../api";
import { Button, Chip, EmptyState, ErrorState, IndicatorList, LoadingState, Modal, PageHeading, Score, Surface, Transcript, formatDate, titleCase, verdictTone } from "../components";
import { usePolling } from "../hooks";
import type { CaseDetail, JsonRecord, RelatedCase } from "../types";

const tabs = ["conversation", "signals", "indicators", "sandbox", "relationships", "evidence"] as const;
type Tab = typeof tabs[number];

export function CaseWorkspace({ source, id, navigate }: { source: "live" | "history"; id: string; navigate: (route: string) => void }) {
  const [tab, setTab] = useState<Tab>(() => {
    const candidate = window.location.hash.replace(/^#\/?/, "").split("/")[3];
    return tabs.includes(candidate as Tab) ? candidate as Tab : "conversation";
  });
  const [confirmStop, setConfirmStop] = useState(false);
  const [busy, setBusy] = useState(false);
  const endpoint = source === "live" ? `/api/sessions/${id}` : `/api/history/${id}`;
  const { data, error, loading, refresh } = usePolling(() => api<CaseDetail>(endpoint), source === "live" ? 2500 : 0, [endpoint]);
  const related = usePolling(() => source === "history" ? api<RelatedCase[]>(`/api/history/${id}/related`) : Promise.resolve([]), 0, [source, id]);
  const evidence = usePolling(() => source === "live" ? Promise.resolve<Record<string, unknown> | null>(null) : api<Record<string, unknown>[]>("/api/evidence").then(rows => rows.find(row => String(row.history_id || row.bundle_id || "").includes(id)) || null), 0, [source, id]);
  const indicators = data?.hvi_items || asArray(data?.case_intelligence?.indicators) || [];
  const signals = data?.signals || asArray(data?.case_intelligence?.signals);
  const risk = Number(data?.score || data?.selected_analysis?.score || 0);

  async function action(name: "resume" | "abandon" | "stop") {
    setBusy(true);
    try {
      const result = await post<Record<string, unknown>>(`/api/sessions/${id}/${name}`);
      if (name === "stop") {
        const url = String(result.download_url || `/api/sessions/${id}/evidence`);
        window.open(authenticatedUrl(url), "_blank", "noopener");
        navigate("cases");
      } else await refresh();
    } catch (reason) { window.alert(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); setConfirmStop(false); }
  }

  if (loading && !data) return <LoadingState label="Opening case workspace" />;
  if (error && !data) return <ErrorState message={error} retry={() => void refresh()} />;
  if (!data) return null;
  return <div className="view workspace-view">
    <PageHeading eyebrow={`${source === "live" ? "Live takeover" : "Sealed case"} · ${data.peer_id || id}`} title={data.name || `Peer ${data.peer_id || id}`} copy={`${titleCase(data.persona)} · ${data.messages?.length || data.message_count || 0} messages · ${formatDate(data.started_ts, true)}`} actions={<><Chip tone={verdictTone(data.verdict)}>{titleCase(data.verdict || "not assessed")}</Chip><Score value={risk}/>{source === "live" && <Button tone="danger" onClick={() => setConfirmStop(true)}>Stop & seal</Button>}</>} />
    <nav className="tab-strip" aria-label="Case workspace">{tabs.map(item => <button key={item} className={tab === item ? "active" : ""} onClick={() => { setTab(item); window.history.replaceState(null, "", `#/case/${source}/${id}/${item}`); }}>{titleCase(item)}{item === "indicators" && indicators.length ? <span>{indicators.length}</span> : null}</button>)}</nav>
    {tab === "conversation" && <div className="workspace-grid"><Surface className="conversation-surface"><Transcript messages={data.messages}/></Surface><aside className="finding-column"><Surface><div className="risk-lockup"><span>Current risk</span><strong>{Math.round(risk * 100)}%</strong><div><i style={{ width: `${Math.max(2, risk * 100)}%` }}/></div><p>{titleCase(data.verdict || "Awaiting assessment")}</p></div></Surface><Surface><h2>Live findings</h2><IndicatorList compact items={indicators.slice(0, 4)}/></Surface>{source === "live" && <Surface><h2>Session controls</h2><div className="button-stack"><Button onClick={() => void action("resume")} disabled={busy}>Resume checkpoint</Button><Button tone="quiet" onClick={() => void action("abandon")} disabled={busy}>Abandon recovery</Button></div></Surface>}</aside></div>}
    {tab === "signals" && <Surface><SectionTitle title="Signals and assessments" copy="Pipeline observations are shown with their recorded provenance."/><RecordGrid items={[...signals, ...(data.assessment_history || [])]}/></Surface>}
    {tab === "indicators" && <Surface><SectionTitle title="Extracted indicators" copy="Values are display-only until a correction endpoint is introduced."/><IndicatorList items={indicators}/></Surface>}
    {tab === "sandbox" && <Surface><SectionTitle title="Sandbox results" copy="Safe inspection outcomes attached to this case."/><RecordGrid items={data.sandbox_results || []}/></Surface>}
    {tab === "relationships" && <Surface><SectionTitle title="Related scam vectors" copy="Semantic similarity results from the Qdrant pattern index."/>{source === "live" ? <EmptyState title="Available after sealing" copy="Relationships are resolved against sealed case patterns."/> : related.loading ? <LoadingState/> : related.error ? <ErrorState message={related.error}/> : <><RelationshipGraph peer={data.peer_id} items={related.data || []}/><RecordGrid items={related.data || []}/></>}<p className="claim-limit">Semantic similarity is retrieval, not proof of shared ownership. Exact identifier overlap remains the authoritative relationship.</p></Surface>}
    {tab === "evidence" && <Surface><SectionTitle title="Evidence and provenance" copy="Sealed artefacts remain verifiable and downloadable with panel authentication."/>{source === "live" ? <EmptyState title="Evidence is still open" copy="Stop and seal the case to generate its signed evidence bundle."/> : <div className="evidence-callout"><div><strong>Case sealed</strong><p>{evidence.data ? "A signed evidence bundle is available for this case." : "Review the Evidence vault for the generated bundle."}</p></div><Button onClick={() => navigate("vault")}>Open vault</Button></div>}</Surface>}
    {confirmStop && <Modal danger title="Stop and seal this case?" copy="HIVE will stop responding, persist the transcript, and generate the evidence package. This cannot be resumed." onClose={() => setConfirmStop(false)} actions={<><Button onClick={() => setConfirmStop(false)}>Keep running</Button><Button tone="danger" disabled={busy} onClick={() => void action("stop")}>{busy ? "Sealing…" : "Stop & seal"}</Button></>}><p>The signed bundle will open in a new tab when sealing completes.</p></Modal>}
  </div>;
}

function asArray(value: unknown): JsonRecord[] { return Array.isArray(value) ? value.filter(item => item && typeof item === "object") as JsonRecord[] : []; }
function SectionTitle({ title, copy }: { title: string; copy: string }) { return <header className="content-title"><h2>{title}</h2><p>{copy}</p></header>; }
function RecordGrid({ items }: { items: JsonRecord[] }) {
  if (!items.length) return <EmptyState title="Nothing recorded" copy="This case does not contain any entries for this view."/>;
  return <div className="record-grid">{items.map((item, index) => <article key={index}>{Object.entries(item).filter(([,value]) => ["string","number","boolean"].includes(typeof value)).slice(0, 8).map(([key,value]) => <div key={key}><span>{titleCase(key)}</span><strong>{String(value)}</strong></div>)}</article>)}</div>;
}

function RelationshipGraph({ peer, items }: { peer?: number; items: RelatedCase[] }) {
  if (!items.length) return <EmptyState title="No related cases" copy="The pattern index did not return a candidate for this sealed case."/>;
  const shown = items.slice(0, 8);
  const center = {x:300,y:145};
  return <div className="relationship-graph"><header><span><i className="exact"/>Exact identifier overlap</span><span><i className="candidate"/>Semantic candidate</span></header><svg viewBox="0 0 600 290" role="img" aria-label={`Relationship graph for peer ${peer || "unknown"}`}><g>{shown.map((item,index) => { const angle=(Math.PI*2*index/shown.length)-Math.PI/2; const x=center.x+Math.cos(angle)*205; const y=center.y+Math.sin(angle)*105; const exact=String(item.relationship || item.match_type || "").includes("identifier") || Boolean(item.authoritative); return <g key={item.related_history_id || index}><line x1={center.x} y1={center.y} x2={x} y2={y} className={exact ? "exact" : "candidate"}/><circle cx={x} cy={y} r="28" className={exact ? "exact" : "candidate"}/><text x={x} y={y+4} textAnchor="middle">{String(item.peer_id || "case").slice(-7)}</text></g>;})}<circle cx={center.x} cy={center.y} r="38" className="current"/><text x={center.x} y={center.y+4} textAnchor="middle">{String(peer || "current").slice(-7)}</text></g></svg></div>;
}
