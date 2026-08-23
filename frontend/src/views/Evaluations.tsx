import { useState } from "react";
import { api, authenticatedUrl } from "../api";
import { Button, Chip, EmptyState, ErrorState, IndicatorList, LoadingState, Modal, PageHeading, Score, Surface, Transcript, formatDate, titleCase, verdictTone } from "../components";
import { usePolling } from "../hooks";
import type { EvaluationRow } from "../types";

export function EvaluationsView() {
  const [detail, setDetail] = useState<EvaluationRow | null>(null);
  const [detailError, setDetailError] = useState("");
  const { data, error, loading, refresh } = usePolling(() => api<EvaluationRow[]>("/api/evaluations"), 10000, []);
  async function open(row: EvaluationRow) { try { setDetailError(""); setDetail(await api<EvaluationRow>(`/api/evaluations/${row.id}`)); } catch (reason) { setDetailError(reason instanceof Error ? reason.message : String(reason)); } }
  if (loading && !data) return <LoadingState label="Loading evaluation runs"/>;
  if (error && !data) return <ErrorState message={error} retry={() => void refresh()}/>;
  return <div className="view"><PageHeading eyebrow="Quality assurance" title="Evaluations" copy="Compare repeatable test runs, extraction quality, decisions, and sealed evidence."/>
    <Surface>{data?.length ? <div className="evaluation-table"><div className="table-head"><span>Scenario</span><span>Decision</span><span>Score</span><span>F1</span><span>Evidence</span><span>Recorded</span></div>{data.map(row => <button key={row.id} onClick={() => void open(row)}><span><strong>{titleCase(row.scenario)}</strong><small>{titleCase(row.persona)} · {row.language || "—"}</small></span><Chip tone={verdictTone(row.verdict)}>{titleCase(row.verdict)}</Chip><Score value={row.verdict_score}/><span>{typeof row.f1 === "number" ? row.f1.toFixed(2) : "—"}</span><Chip tone={row.evidence_verified ? "success" : "warning"}>{row.evidence_verified ? "Verified" : "Check"}</Chip><time>{formatDate(row.recorded_ts, true)}</time></button>)}</div> : <EmptyState title="No evaluation runs" copy="Recorded evaluation packs will appear here after the test runner completes."/>}</Surface>
    {(detail || detailError) && <Modal title={detail ? `${titleCase(detail.scenario)} evaluation` : "Evaluation unavailable"} copy={detail ? `${detail.id} · ${formatDate(detail.recorded_ts, true)}` : detailError} onClose={() => {setDetail(null); setDetailError("");}} actions={detail?.package_download_url ? <Button tone="primary" onClick={() => window.open(authenticatedUrl(detail.package_download_url!), "_blank", "noopener")}>Download evidence</Button> : undefined}>{detail && <div className="evaluation-detail"><div className="metric-row"><div><span>Verdict</span><strong>{titleCase(detail.verdict)}</strong></div><div><span>Score</span><strong>{detail.verdict_score?.toFixed(2) || "—"}</strong></div><div><span>F1</span><strong>{detail.f1?.toFixed(2) || "—"}</strong></div></div>{detail.transcript && <Transcript messages={detail.transcript.map(([role,text], index) => ({role,text,msg_id:index}))}/>}<IndicatorList items={detail.hvi_items}/></div>}</Modal>}
  </div>;
}
