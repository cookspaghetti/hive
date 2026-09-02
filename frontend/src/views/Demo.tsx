import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, authenticatedUrl, post } from "../api";
import { Button, Chip, EmptyState, ErrorState, IndicatorList, LoadingState, PageHeading, Surface, Transcript, titleCase } from "../components";
import { usePolling } from "../hooks";
import type { DemoRun, DemoScenario, Indicator, JsonRecord, MessageItem } from "../types";
import { AnalysisSignals, SandboxResults, ThreatIntelligencePanel } from "./CaseWorkspace";

export function DemoView() {
  const [scenario, setScenario] = useState("investment");
  const [persona, setPersona] = useState("confused_elderly");
  const [speed, setSpeed] = useState("normal");
  const [mode, setMode] = useState("scripted");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [displayRunId, setDisplayRunId] = useState("");
  const [dismissedRunId, setDismissedRunId] = useState("");
  const [scenarioSearch, setScenarioSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [languageFilter, setLanguageFilter] = useState("all");
  const [contentFilter, setContentFilter] = useState("all");
  const [executionFilter, setExecutionFilter] = useState("all");
  const catalog = usePolling(() => api<JsonRecord>("/api/demo/scenarios"), 0, []);
  const runs = usePolling(() => api<DemoRun[]>("/api/demo/runs"), 2500, []);
  const activeStates = ["running","paused","processing","awaiting_input","sealing","stopping"];
  const activeSummary = useMemo(() => runs.data?.find(run => activeStates.includes(String(run.status).toLowerCase())), [runs.data]);
  const recoveredRunId = String(activeSummary?.id || activeSummary?.run_id || "");
  const activeId = displayRunId || (recoveredRunId !== dismissedRunId ? recoveredRunId : "");
  const current = usePolling(() => activeId ? api<DemoRun>(`/api/demo/runs/${activeId}`) : Promise.resolve(null), activeId ? 1500 : 0, [activeId]);
  const scenarios = normaliseCatalog(catalog.data || undefined);
  const categories = unique(scenarios.map(item => String(item.category || "Other")));
  const languages = unique(scenarios.map(item => String(item.language || "Other")));
  const contentTypes = unique(scenarios.flatMap(item => asStrings(item.content_types)));
  const query = scenarioSearch.trim().toLowerCase();
  const visibleScenarios = scenarios.filter(item => {
    const searchable = [item.title,item.name,item.description,item.category,item.archetype,item.language,item.reference,...asStrings(item.content_types),...asStrings(item.tags)].join(" ").toLowerCase();
    return (!query || searchable.includes(query))
      && (categoryFilter === "all" || item.category === categoryFilter)
      && (languageFilter === "all" || item.language === languageFilter)
      && (contentFilter === "all" || asStrings(item.content_types).includes(contentFilter))
      && (executionFilter === "all" || (executionFilter === "live") === Boolean(item.live_services));
  });
  const filtersActive = Boolean(query || categoryFilter !== "all" || languageFilter !== "all" || contentFilter !== "all" || executionFilter !== "all");
  const selectedScenario = scenarios.find(item => String(item.key || item.id) === scenario);
  const liveShowcase = Boolean(selectedScenario?.live_services);
  useEffect(() => { if (!displayRunId && recoveredRunId && recoveredRunId !== dismissedRunId) setDisplayRunId(recoveredRunId); }, [displayRunId, dismissedRunId, recoveredRunId]);
  useEffect(() => { if (liveShowcase && mode !== "scripted") setMode("scripted"); }, [liveShowcase, mode]);

  async function start() { setBusy(true); try { const created = await post<DemoRun>("/api/demo/runs", {scenario, persona, speed, mode}); const createdId = String(created.id || created.run_id || ""); setDismissedRunId(""); if (createdId) { current.setData(created); setDisplayRunId(createdId); } await runs.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function control(action: string) { if (!activeId) return; setBusy(true); try { await post(`/api/demo/runs/${activeId}/${action}`); await Promise.all([runs.refresh(), current.refresh()]); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function changeScenario() { if (!activeId) return; setBusy(true); try { if (activeStates.includes(String(current.data?.status || activeSummary?.status).toLowerCase())) await post(`/api/demo/runs/${activeId}/stop`); setDismissedRunId(activeId); setDisplayRunId(""); current.setData(null); await runs.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }
  async function submit(event: FormEvent) { event.preventDefault(); if (!message.trim() || !activeId) return; setBusy(true); try { await post(`/api/demo/runs/${activeId}/messages`, {text: message.trim()}); setMessage(""); await current.refresh(); } catch (reason) { window.alert(toMessage(reason)); } finally { setBusy(false); } }

  if ((catalog.loading || runs.loading) && !catalog.data && !runs.data) return <LoadingState label="Opening demo lab"/>;
  if ((catalog.error || runs.error) && !catalog.data && !runs.data) return <ErrorState message={catalog.error || runs.error || "Demo lab unavailable"}/>;
  const currentId = String(current.data?.id || current.data?.run_id || "");
  const candidate = currentId === activeId ? current.data : recoveredRunId === activeId ? activeSummary : null;
  const run = activeId ? candidate : null;
  return <div className={`view demo-view ${run ? "demo-run-view" : ""}`}><PageHeading eyebrow="Isolated environment" title="Demo lab" copy="Run repeatable scam conversations through the live analysis pipeline without sending Telegram messages." actions={<><Chip>Isolated transcripts</Chip><Chip tone="success">Telegram disconnected</Chip></>}/>
    {run ? <RunningDemo run={run} busy={busy} onControl={control} onExit={changeScenario} message={message} setMessage={setMessage} submit={submit}/> : <>
      <Surface className="demo-controls">
        <label><span>Mode</span><select value={liveShowcase?"scripted":mode} disabled={liveShowcase} onChange={event => setMode(event.target.value)}><option value="scripted">Scripted scammer</option><option value="model_driven">Model-driven scammer</option><option value="interactive">Interactive presenter</option></select></label>
        <label><span>Victim persona</span><select value={persona} onChange={event => setPersona(event.target.value)}><option value="confused_elderly">Confused elderly</option><option value="naive_young_adult">Naive young adult</option><option value="overseas_worker">Overseas worker</option><option value="small_business_owner">Small business owner</option></select></label>
        <label><span>Playback speed</span><select value={speed} onChange={event => setSpeed(event.target.value)}><option value="normal">Normal</option><option value="2x">2×</option><option value="5x">5×</option><option value="step">Step-through</option></select></label>
        <Button tone="primary" disabled={busy || !scenarios.length} onClick={() => void start()}>{busy ? "Starting…" : liveShowcase ? "Start OSINT showcase" : "Start live demo"}</Button>
        <small className="demo-controls-note">{liveShowcase?"Locked: only hardcoded public observables may reach external services.":"Choose how the scammer side is controlled."}</small>
      </Surface>
      <Surface className="scenario-filters">
        <label className="scenario-search"><span>Find a scenario</span><input type="search" value={scenarioSearch} onChange={event=>setScenarioSearch(event.target.value)} placeholder="Search loan, parcel, OTP, Malay…"/></label>
        <label><span>Category</span><select value={categoryFilter} onChange={event=>setCategoryFilter(event.target.value)}><option value="all">All categories</option>{categories.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
        <label><span>Language</span><select value={languageFilter} onChange={event=>setLanguageFilter(event.target.value)}><option value="all">All languages</option>{languages.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
        <label><span>Content</span><select value={contentFilter} onChange={event=>setContentFilter(event.target.value)}><option value="all">All content</option>{contentTypes.map(value=><option key={value} value={value}>{titleCase(value)}</option>)}</select></label>
        <label><span>Execution</span><select value={executionFilter} onChange={event=>setExecutionFilter(event.target.value)}><option value="all">All execution</option><option value="synthetic">Synthetic only</option><option value="live">Live OSINT only</option></select></label>
        <div className="scenario-filter-summary"><strong>{visibleScenarios.length} of {scenarios.length}</strong><span>scenarios</span><Button tone="quiet" disabled={!filtersActive} onClick={()=>{setScenarioSearch("");setCategoryFilter("all");setLanguageFilter("all");setContentFilter("all");setExecutionFilter("all");}}>Reset</Button></div>
      </Surface>
      {visibleScenarios.length ? <div className="scenario-grid">{visibleScenarios.map(item => <button key={item.key || item.id} className={`${scenario === (item.key || item.id) ? "active" : ""} ${item.live_services ? "live-service" : ""}`} onClick={() => setScenario(String(item.key || item.id))}><p className="eyebrow">{item.live_services ? "Provider-backed showcase" : item.exchanges ? `${String(item.exchanges)} exchanges` : "Scripted exchanges"}</p><h2>{item.title || item.name}</h2><p>{item.description || "A synthetic fixture for repeatable pipeline testing."}</p><div>{[item.language, item.category || item.archetype, Number(item.attachments || 0) ? `${item.attachments} attachment${Number(item.attachments) === 1 ? "" : "s"}` : "", ...asStrings(item.content_types), ...asStrings(item.tags)].filter(Boolean).map(tag => <Chip key={String(tag)} tone={item.live_services&&String(tag)==="Live OSINT"?"info":undefined}>{String(tag)}</Chip>)}</div></button>)}</div> : <Surface><EmptyState title="No matching scenarios" copy="Try a broader search or reset the scenario filters." action={<Button onClick={()=>{setScenarioSearch("");setCategoryFilter("all");setLanguageFilter("all");setContentFilter("all");setExecutionFilter("all");}}>Reset filters</Button>}/></Surface>}
    </>}
  </div>;
}

function RunningDemo({ run, busy, onControl, onExit, message, setMessage, submit }: { run: DemoRun; busy: boolean; onControl: (action: string) => void; onExit: () => void; message: string; setMessage: (value: string) => void; submit: (event: FormEvent) => void }) {
  const status = String(run.status || run.stage || "running");
  const paused = status.toLowerCase() === "paused";
  const exchange = Number(run.exchange || run.current_exchange || 0);
  const [sheet,setSheet] = useState(false);
  const [tab,setTab] = useState<"conversation"|"signals"|"indicators"|"intelligence"|"sandbox">("conversation");
  const scenario = typeof run.scenario === "object" && run.scenario ? String(run.scenario.title || run.scenario.key || "Demo") : String(run.scenario || "Demo");
  const indicators = run.hvi_items || [];
  const sandboxRuns = Array.isArray(run.sandbox_results) ? run.sandbox_results.length : 0;
  const intelligence = run.threat_intelligence || [];
  const signals = asRecords(run.signal_trail);
  const messages = run.messages || [];
  const media = messages.flatMap(message => {
    const value = message.media_analysis;
    if (Array.isArray(value)) return value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object");
    return value && typeof value === "object" ? [value as JsonRecord] : [];
  });
  const liveServices = Boolean(run.live_services);
  const running = ["running","processing","sealing","stopping"].includes(status.toLowerCase());
  const complete = !["running","paused","processing","awaiting_input","sealing","stopping"].includes(status.toLowerCase());
  const stageProgress = complete ? 4 : Math.min(exchange,3);
  const risk = Number(run.score || run.verdict_score || 0);
  const stages=liveServices?["Persona reply","Indicator extraction","Disposable sandbox + OSINT","Complete showcase"]:["Persona reply","Media + indicator extraction","Reserved URL sandbox","Seal and verify"];
  const tabs=[{key:"conversation",label:"Conversation",count:messages.length},{key:"signals",label:"Signals",count:signals.length},{key:"indicators",label:"Indicators",count:indicators.length},{key:"intelligence",label:"Intelligence",count:intelligence.length},{key:"sandbox",label:"Sandbox",count:sandboxRuns}] as const;
  return <><Surface className="pipeline"><div className="pipeline-head"><div><p className="eyebrow">{liveServices?"Provider-backed showcase":"Live synthetic run"}</p><h2>{scenario} · {titleCase(run.mode)}</h2></div><Chip tone={liveServices?"info":"success"}>{titleCase(status)}</Chip></div><div className="pipeline-stages">{stages.map((stage,index) => <div className={index < stageProgress ? "done" : index === stageProgress ? "active" : ""} key={stage}><span>{index + 1}</span><strong>{stage}</strong></div>)}</div><div className="playback"><div className="button-stack"><Button onClick={() => onControl(paused ? "resume" : "pause")} disabled={busy || complete}>{paused ? "Resume" : "Pause"}</Button><Button onClick={() => onControl("advance")} disabled={busy || complete}>Next exchange</Button></div><div className="button-stack"><Button onClick={onExit} disabled={busy}>Change scenario</Button><Button tone="danger" onClick={() => onControl("stop")} disabled={busy || complete}>Stop</Button></div></div></Surface><nav className="tab-strip demo-analysis-tabs" aria-label="Demo analysis">{tabs.map(item=><button key={item.key} className={tab===item.key?"active":""} onClick={()=>setTab(item.key)}>{item.label}<span>{item.count}</span></button>)}</nav>{tab==="conversation"&&<div className="workspace-grid conversation-grid demo-conversation-grid"><Surface className="conversation-surface"><Transcript messages={messages} thinking={running}/>{run.mode === "interactive" && !complete && <form className="message-composer" onSubmit={submit}><input value={message} onChange={event => setMessage(event.target.value)} placeholder="Type the scammer's next message"/><Button tone="primary" disabled={busy || !message.trim()} type="submit">Send</Button></form>}<div className="pacing-status" role="status"><span className={`status-dot ${paused?"warning":running?"live":"success"}`}/><span><strong>{paused?"Demo paused":running?String(run.stage||"Running the isolated pipeline"):liveServices?"OSINT showcase complete":"Synthetic run complete"}</strong><small>{paused?"Resume or advance one exchange when ready.":complete?"Results stay here until you change scenario or refresh.":"Every bubble and attachment is retained in this isolated run."}</small></span><small>{exchange}/{Number(run.total_exchanges||0)||"—"} exchanges</small></div></Surface><aside className={`finding-column ${sheet?"sheet-open":""}`}><button className="tablet-findings-handle" onClick={()=>setSheet(value=>!value)}>Live findings · {indicators.length} indicators · {sandboxRuns} sandbox runs <span>{sheet?"⌄":"⌃"}</span></button><Surface><div className="risk-lockup"><span>{complete?"Final verdict":"Running verdict"}</span><strong>{Math.round(risk*100)}%</strong><div><i style={{width:`${Math.max(2,risk*100)}%`}}/></div><p>{titleCase(run.verdict || "Not assessed")}</p></div></Surface><Surface><h2>{complete?"Final findings":"Live findings"}</h2><IndicatorList compact items={indicators.slice().reverse().slice(0,5)}/></Surface>{intelligence.length>0&&<Surface className="demo-intelligence"><h2>{liveServices?"Live provider results":"OSINT routing"}</h2><p className="claim-limit">{liveServices?"Only the hardcoded public showcase observables are queried.":"Synthetic fixtures never leave HIVE."}</p><div>{intelligence.slice(-5).reverse().map((item,index)=><span key={item.id||index}><i className={`status-dot ${item.status==="error"?"warning":"success"}`}/><strong>{item.provider_label||titleCase(item.provider)}</strong><small>{titleCase(item.risk||item.indicator_kind)}</small></span>)}</div></Surface>}</aside></div>}{tab==="signals"&&<AnalysisSignals items={signals} messages={messages}/>} {tab==="indicators"&&<DemoIndicators items={indicators} media={media} messages={messages}/>} {tab==="intelligence"&&<ThreatIntelligencePanel source="history" caseId={String(run.id||"")} items={intelligence} context="demo" onUpdated={async()=>undefined}/>} {tab==="sandbox"&&<SandboxResults items={asRecords(run.sandbox_results)}/>}</>;
}

function DemoIndicators({items,media,messages}:{items:Indicator[];media:JsonRecord[];messages:MessageItem[]}){
  return <div className="content-stack"><Surface><div className="content-title"><h2>Extracted indicators</h2><p>Read-only pipeline findings with extractor confidence and source-message provenance.</p></div>{items.length?<div className="indicator-table demo-indicator-table"><header><span>Type</span><span>Value</span><span>Extractor</span><span>Confidence</span><span>Source</span><span>State</span></header>{items.map((item,index)=><article key={`${item.kind}-${item.value}-${index}`}><span>{titleCase(item.kind)}</span><code>{item.value||"—"}</code><span>{item.extractor||"pipeline"}</span><strong>{typeof item.confidence==="number"?item.confidence.toFixed(2):"—"}</strong><span>Message {item.source_msg_id??"—"}<small>{messages.find(message=>message.msg_id===item.source_msg_id)?.text}</small></span><span className="indicator-state"><i className={`status-dot ${Number(item.confidence||0)>=.7?"success":"warning"}`}/>{Number(item.confidence||0)>=.7?"Auto-accepted":"Review context"}</span></article>)}</div>:<EmptyState title="No indicators extracted" copy="Run the next exchange or choose a scenario containing a structured observable."/>}<p className="claim-limit">Demo indicators are read-only and never create authoritative case relationships.</p></Surface><Surface><div className="content-title"><h2>Local media extraction</h2><p>Received images and documents remain tied to their source message while QR/OCR and safe fixture analysis run locally.</p></div>{media.length?<div className="media-finding-grid">{media.map((item,index)=>{const sourceMessage=messages.find(message=>Number(message.msg_id)===Number(item.source_msg_id));return <article key={`${item.source_msg_id||index}-${item.media_sha256||index}`}>{sourceMessage?.media_url&&sourceMessage.media_mime?.startsWith("image/")?<img src={authenticatedUrl(sourceMessage.media_url)} alt={sourceMessage.media_name||"Synthetic message attachment"}/>:<div className="media-placeholder">{sourceMessage?.media_kind==="document"?"PDF":"Local extraction record"}</div>}<div><Chip tone="success">{titleCase(item.source||"local extraction")}</Chip><h3>{String(item.description||"Media finding")}</h3><p>Message {String(item.source_msg_id||"—")} · {String(sourceMessage?.media_sha256||item.media_sha256||"hash not reported")}</p></div></article>})}</div>:<EmptyState title="No media findings" copy="Image, document, QR, OCR and inert-file findings appear here when the selected scenario contains an attachment."/>}</Surface></div>;
}

function normaliseCatalog(data?: JsonRecord): DemoScenario[] { if (!data) return []; const value = Array.isArray(data) ? data : Array.isArray(data.scenarios) ? data.scenarios : Object.entries(data).map(([key,item]) => typeof item === "object" && item ? {key,...item as object} : {key,title:String(item)}); return value as DemoScenario[]; }
function asStrings(value: unknown): string[] { return Array.isArray(value) ? value.map(String) : []; }
function unique(values: string[]): string[] { return [...new Set(values.filter(Boolean))].sort((left,right)=>left.localeCompare(right)); }
function asRecords(value: unknown): JsonRecord[] { return Array.isArray(value) ? value.filter((item):item is JsonRecord=>Boolean(item)&&typeof item==="object") : []; }
function toMessage(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }
