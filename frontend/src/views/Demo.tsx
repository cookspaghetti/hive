import { FormEvent, useMemo, useState } from "react";
import { api, authenticatedUrl, post } from "../api";
import { Button, Chip, EmptyState, ErrorState, IndicatorList, LoadingState, PageHeading, Surface, Transcript, titleCase, verdictTone } from "../components";
import { usePolling } from "../hooks";
import type { DemoRun, DemoScenario, JsonRecord } from "../types";

export function DemoView() {
  const [scenario, setScenario] = useState("investment");
  const [persona, setPersona] = useState("confused_elderly");
  const [speed, setSpeed] = useState("normal");
  const [mode, setMode] = useState("scripted");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const catalog = usePolling(() => api<JsonRecord>("/api/demo/scenarios"), 0, []);
  const runs = usePolling(() => api<DemoRun[]>("/api/demo/runs"), 2500, []);
  const activeStates = ["running","paused","processing","awaiting_input","sealing","stopping"];
  const activeSummary = useMemo(() => runs.data?.find(run => activeStates.includes(String(run.status).toLowerCase())), [runs.data]);
  const activeId = String(activeSummary?.id || activeSummary?.run_id || "");
  const current = usePolling(() => activeId ? api<DemoRun>(`/api/demo/runs/${activeId}`) : Promise.resolve(null), activeId ? 1500 : 0, [activeId]);
  const scenarios = normaliseCatalog(catalog.data || undefined);

  async function start() { setBusy(true); try { await post("/api/demo/runs", {scenario, persona, speed, mode}); await runs.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function control(action: string) { if (!activeId) return; setBusy(true); try { await post(`/api/demo/runs/${activeId}/${action}`); await Promise.all([runs.refresh(), current.refresh()]); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function submit(event: FormEvent) { event.preventDefault(); if (!message.trim() || !activeId) return; setBusy(true); try { await post(`/api/demo/runs/${activeId}/messages`, {text: message.trim()}); setMessage(""); await current.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }

  if ((catalog.loading || runs.loading) && !catalog.data && !runs.data) return <LoadingState label="Opening demo lab"/>;
  if ((catalog.error || runs.error) && !catalog.data && !runs.data) return <ErrorState message={catalog.error || runs.error || "Demo lab unavailable"}/>;
  const candidate = current.data || activeSummary;
  const run = activeId && activeStates.includes(String(candidate?.status).toLowerCase()) ? candidate : null;
  return <div className="view demo-view"><PageHeading eyebrow="Isolated environment" title="Demo lab" copy="Run repeatable scam conversations through the live analysis pipeline without sending Telegram messages." actions={<><Chip>Synthetic fixtures</Chip><Chip tone="success">Telegram disconnected</Chip></>}/>
    {run ? <RunningDemo run={run} busy={busy} onControl={control} message={message} setMessage={setMessage} submit={submit}/> : <><div className="scenario-grid">{scenarios.map(item => <button key={item.key || item.id} className={scenario === (item.key || item.id) ? "active" : ""} onClick={() => setScenario(String(item.key || item.id))}><p className="eyebrow">{item.exchanges ? `${String(item.exchanges)} exchanges` : "Scripted exchanges"}</p><h2>{item.title || item.name}</h2><p>{item.description || "A synthetic fixture for repeatable pipeline testing."}</p><div>{[item.language, item.archetype, ...asStrings(item.tags)].filter(Boolean).map(tag => <Chip key={String(tag)}>{String(tag)}</Chip>)}</div></button>)}</div><Surface className="demo-controls"><label><span>Mode</span><select value={mode} onChange={event => setMode(event.target.value)}><option value="scripted">Scripted scammer</option><option value="model_driven">Model-driven scammer</option><option value="interactive">Interactive presenter</option></select></label><label><span>Victim persona</span><select value={persona} onChange={event => setPersona(event.target.value)}><option value="confused_elderly">Confused elderly</option><option value="naive_young_adult">Naive young adult</option><option value="overseas_worker">Overseas worker</option><option value="small_business_owner">Small business owner</option></select></label><label><span>Playback speed</span><select value={speed} onChange={event => setSpeed(event.target.value)}><option value="normal">Normal</option><option value="2x">2×</option><option value="5x">5×</option><option value="step">Step-through</option></select></label><Button tone="primary" disabled={busy || !scenarios.length} onClick={() => void start()}>{busy ? "Starting…" : "Start live demo"}</Button></Surface></>}
    <Surface><header className="surface-header"><div><h2>Previous demo runs</h2><p>Recorded synthetic evidence remains separate from real cases.</p></div></header>{runs.data?.length ? <div className="run-list">{runs.data.map(item => <article key={item.id || item.run_id}><div><strong>{titleCase(item.scenario)}</strong><p>{titleCase(item.mode)} · {titleCase(item.persona)}</p></div><Chip tone={verdictTone(item.verdict)}>{titleCase(item.verdict || item.status)}</Chip>{item.evidence_download_url && <Button onClick={() => window.open(authenticatedUrl(item.evidence_download_url!), "_blank", "noopener")}>Evidence</Button>}</article>)}</div> : <EmptyState title="No demo runs" copy="A completed synthetic run will appear here."/>}</Surface>
  </div>;
}

function RunningDemo({ run, busy, onControl, message, setMessage, submit }: { run: DemoRun; busy: boolean; onControl: (action: string) => void; message: string; setMessage: (value: string) => void; submit: (event: FormEvent) => void }) {
  const status = String(run.status || run.stage || "running");
  const paused = status.toLowerCase() === "paused";
  const exchange = Number(run.exchange || run.current_exchange || 0);
  return <><Surface className="pipeline"><div className="pipeline-head"><div><p className="eyebrow">Live synthetic run</p><h2>{titleCase(run.scenario)} · {titleCase(run.mode)}</h2></div><Chip tone="success">{titleCase(status)}</Chip></div><div className="pipeline-stages">{["Persona reply","Extraction","Sandbox stub","Seal and verify"].map((stage,index) => <div className={index < exchange ? "done" : index === exchange ? "active" : ""} key={stage}><span>{index + 1}</span><strong>{stage}</strong></div>)}</div><div className="playback"><div className="button-stack"><Button onClick={() => onControl(paused ? "resume" : "pause")} disabled={busy}>{paused ? "Resume" : "Pause"}</Button><Button onClick={() => onControl("advance")} disabled={busy}>Next exchange</Button></div><div className="button-stack"><Button onClick={() => onControl("stop")} disabled={busy}>Change scenario</Button><Button tone="danger" onClick={() => onControl("stop")} disabled={busy}>Stop</Button></div></div></Surface><div className="workspace-grid"><Surface><Transcript messages={run.messages} thinking={status.toLowerCase() === "running"}/>{run.mode === "interactive" && <form className="message-composer" onSubmit={submit}><input value={message} onChange={event => setMessage(event.target.value)} placeholder="Type the scammer's next message"/><Button tone="primary" disabled={busy || !message.trim()} type="submit">Send</Button></form>}</Surface><aside className="finding-column"><Surface><div className="risk-lockup"><span>Running verdict</span><strong>{Math.round(Number(run.score || run.verdict_score || 0) * 100)}%</strong><p>{titleCase(run.verdict || "Not assessed")}</p></div></Surface><Surface><h2>Live findings</h2><IndicatorList compact items={run.hvi_items}/></Surface></aside></div></>;
}

function normaliseCatalog(data?: JsonRecord): DemoScenario[] { if (!data) return []; const value = Array.isArray(data) ? data : Array.isArray(data.scenarios) ? data.scenarios : Object.entries(data).map(([key,item]) => typeof item === "object" && item ? {key,...item as object} : {key,title:String(item)}); return value as DemoScenario[]; }
function asStrings(value: unknown): string[] { return Array.isArray(value) ? value.map(String) : []; }
function toMessage(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }
