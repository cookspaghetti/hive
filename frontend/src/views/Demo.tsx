import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, post } from "../api";
import { Button, Chip, ErrorState, IndicatorList, LoadingState, PageHeading, Surface, Transcript, titleCase } from "../components";
import { usePolling } from "../hooks";
import type { DemoRun, DemoScenario, JsonRecord } from "../types";

export function DemoView() {
  const [scenario, setScenario] = useState("investment");
  const [persona, setPersona] = useState("confused_elderly");
  const [speed, setSpeed] = useState("normal");
  const [mode, setMode] = useState("scripted");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [displayRunId, setDisplayRunId] = useState("");
  const [dismissedRunId, setDismissedRunId] = useState("");
  const catalog = usePolling(() => api<JsonRecord>("/api/demo/scenarios"), 0, []);
  const runs = usePolling(() => api<DemoRun[]>("/api/demo/runs"), 2500, []);
  const activeStates = ["running","paused","processing","awaiting_input","sealing","stopping"];
  const activeSummary = useMemo(() => runs.data?.find(run => activeStates.includes(String(run.status).toLowerCase())), [runs.data]);
  const recoveredRunId = String(activeSummary?.id || activeSummary?.run_id || "");
  const activeId = displayRunId || (recoveredRunId !== dismissedRunId ? recoveredRunId : "");
  const current = usePolling(() => activeId ? api<DemoRun>(`/api/demo/runs/${activeId}`) : Promise.resolve(null), activeId ? 1500 : 0, [activeId]);
  const scenarios = normaliseCatalog(catalog.data || undefined);
  useEffect(() => { if (!displayRunId && recoveredRunId && recoveredRunId !== dismissedRunId) setDisplayRunId(recoveredRunId); }, [displayRunId, dismissedRunId, recoveredRunId]);

  async function start() { setBusy(true); try { const created = await post<DemoRun>("/api/demo/runs", {scenario, persona, speed, mode}); const createdId = String(created.id || created.run_id || ""); setDismissedRunId(""); if (createdId) { current.setData(created); setDisplayRunId(createdId); } await runs.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function control(action: string) { if (!activeId) return; setBusy(true); try { await post(`/api/demo/runs/${activeId}/${action}`); await Promise.all([runs.refresh(), current.refresh()]); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function changeScenario() { if (!activeId) return; setBusy(true); try { if (activeStates.includes(String(current.data?.status || activeSummary?.status).toLowerCase())) await post(`/api/demo/runs/${activeId}/stop`); setDismissedRunId(activeId); setDisplayRunId(""); current.setData(null); await runs.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function submit(event: FormEvent) { event.preventDefault(); if (!message.trim() || !activeId) return; setBusy(true); try { await post(`/api/demo/runs/${activeId}/messages`, {text: message.trim()}); setMessage(""); await current.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }

  if ((catalog.loading || runs.loading) && !catalog.data && !runs.data) return <LoadingState label="Opening demo lab"/>;
  if ((catalog.error || runs.error) && !catalog.data && !runs.data) return <ErrorState message={catalog.error || runs.error || "Demo lab unavailable"}/>;
  const currentId = String(current.data?.id || current.data?.run_id || "");
  const candidate = currentId === activeId ? current.data : recoveredRunId === activeId ? activeSummary : null;
  const run = activeId ? candidate : null;
  return <div className={`view demo-view ${run ? "demo-run-view" : ""}`}><PageHeading eyebrow="Isolated environment" title="Demo lab" copy="Run repeatable scam conversations through the live analysis pipeline without sending Telegram messages." actions={<><Chip>Synthetic fixtures</Chip><Chip tone="success">Telegram disconnected</Chip></>}/>
    {run ? <RunningDemo run={run} busy={busy} onControl={control} onExit={changeScenario} message={message} setMessage={setMessage} submit={submit}/> : <><div className="scenario-grid">{scenarios.map(item => <button key={item.key || item.id} className={scenario === (item.key || item.id) ? "active" : ""} onClick={() => setScenario(String(item.key || item.id))}><p className="eyebrow">{item.exchanges ? `${String(item.exchanges)} exchanges` : "Scripted exchanges"}</p><h2>{item.title || item.name}</h2><p>{item.description || "A synthetic fixture for repeatable pipeline testing."}</p><div>{[item.language, item.archetype, Number(item.attachments || 0) ? `${item.attachments} attachment${Number(item.attachments) === 1 ? "" : "s"}` : "", ...asStrings(item.content_types), ...asStrings(item.tags)].filter(Boolean).map(tag => <Chip key={String(tag)}>{String(tag)}</Chip>)}</div></button>)}</div><Surface className="demo-controls"><label><span>Mode</span><select value={mode} onChange={event => setMode(event.target.value)}><option value="scripted">Scripted scammer</option><option value="model_driven">Model-driven scammer</option><option value="interactive">Interactive presenter</option></select></label><label><span>Victim persona</span><select value={persona} onChange={event => setPersona(event.target.value)}><option value="confused_elderly">Confused elderly</option><option value="naive_young_adult">Naive young adult</option><option value="overseas_worker">Overseas worker</option><option value="small_business_owner">Small business owner</option></select></label><label><span>Playback speed</span><select value={speed} onChange={event => setSpeed(event.target.value)}><option value="normal">Normal</option><option value="2x">2×</option><option value="5x">5×</option><option value="step">Step-through</option></select></label><Button tone="primary" disabled={busy || !scenarios.length} onClick={() => void start()}>{busy ? "Starting…" : "Start live demo"}</Button></Surface></>}
  </div>;
}

function RunningDemo({ run, busy, onControl, onExit, message, setMessage, submit }: { run: DemoRun; busy: boolean; onControl: (action: string) => void; onExit: () => void; message: string; setMessage: (value: string) => void; submit: (event: FormEvent) => void }) {
  const status = String(run.status || run.stage || "running");
  const paused = status.toLowerCase() === "paused";
  const exchange = Number(run.exchange || run.current_exchange || 0);
  const [sheet,setSheet] = useState(false);
  const scenario = typeof run.scenario === "object" && run.scenario ? String(run.scenario.title || run.scenario.key || "Demo") : String(run.scenario || "Demo");
  const indicators = run.hvi_items || [];
  const sandboxRuns = Array.isArray(run.sandbox_results) ? run.sandbox_results.length : 0;
  const running = ["running","processing","sealing","stopping"].includes(status.toLowerCase());
  const complete = !["running","paused","processing","awaiting_input","sealing","stopping"].includes(status.toLowerCase());
  const stageProgress = complete ? 4 : Math.min(exchange,3);
  const risk = Number(run.score || run.verdict_score || 0);
  return <><Surface className="pipeline"><div className="pipeline-head"><div><p className="eyebrow">Live synthetic run</p><h2>{scenario} · {titleCase(run.mode)}</h2></div><Chip tone="success">{titleCase(status)}</Chip></div><div className="pipeline-stages">{["Persona reply","Media + indicator extraction","Reserved URL sandbox","Seal and verify"].map((stage,index) => <div className={index < stageProgress ? "done" : index === stageProgress ? "active" : ""} key={stage}><span>{index + 1}</span><strong>{stage}</strong></div>)}</div><div className="playback"><div className="button-stack"><Button onClick={() => onControl(paused ? "resume" : "pause")} disabled={busy || complete}>{paused ? "Resume" : "Pause"}</Button><Button onClick={() => onControl("advance")} disabled={busy || complete}>Next exchange</Button></div><div className="button-stack"><Button onClick={onExit} disabled={busy}>Change scenario</Button><Button tone="danger" onClick={() => onControl("stop")} disabled={busy || complete}>Stop</Button></div></div></Surface><div className="workspace-grid conversation-grid demo-conversation-grid"><Surface className="conversation-surface"><Transcript messages={run.messages} thinking={running}/>{run.mode === "interactive" && !complete && <form className="message-composer" onSubmit={submit}><input value={message} onChange={event => setMessage(event.target.value)} placeholder="Type the scammer's next message"/><Button tone="primary" disabled={busy || !message.trim()} type="submit">Send</Button></form>}<div className="pacing-status" role="status"><span className={`status-dot ${paused?"warning":running?"live":"success"}`}/><span><strong>{paused?"Demo paused":running?String(run.stage||"Running the synthetic pipeline"):"Synthetic run complete"}</strong><small>{paused?"Resume or advance one exchange when ready.":complete?"Results stay here until you change scenario or refresh.":"Every bubble and attachment is retained in this isolated run."}</small></span><small>{exchange}/{Number(run.total_exchanges||0)||"—"} exchanges</small></div></Surface><aside className={`finding-column ${sheet?"sheet-open":""}`}><button className="tablet-findings-handle" onClick={()=>setSheet(value=>!value)}>Live findings · {indicators.length} indicators · {sandboxRuns} sandbox runs <span>{sheet?"⌄":"⌃"}</span></button><Surface><div className="risk-lockup"><span>{complete?"Final verdict":"Running verdict"}</span><strong>{Math.round(risk*100)}%</strong><div><i style={{width:`${Math.max(2,risk*100)}%`}}/></div><p>{titleCase(run.verdict || "Not assessed")}</p></div></Surface><Surface><h2>{complete?"Final findings":"Live findings"}</h2><IndicatorList compact items={indicators.slice().reverse().slice(0,5)}/></Surface></aside></div></>;
}

function normaliseCatalog(data?: JsonRecord): DemoScenario[] { if (!data) return []; const value = Array.isArray(data) ? data : Array.isArray(data.scenarios) ? data.scenarios : Object.entries(data).map(([key,item]) => typeof item === "object" && item ? {key,...item as object} : {key,title:String(item)}); return value as DemoScenario[]; }
function asStrings(value: unknown): string[] { return Array.isArray(value) ? value.map(String) : []; }
function toMessage(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }
