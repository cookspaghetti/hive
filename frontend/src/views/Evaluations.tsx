import { useMemo,useState } from "react";
import { api,authenticatedUrl } from "../api";
import {
  Button,Chip,EmptyState,ErrorState,IndicatorList,LoadingState,Modal,
  PageHeading,Score,Surface,Transcript,formatDate,titleCase,verdictTone,
} from "../components";
import { usePolling } from "../hooks";
import { CharacterReview,characterLabel } from "./evaluationCharacter";
import type { EvaluationRow } from "../types";

function percentage(value:number,total:number){return total?`${(value/total*100).toFixed(1)}%`:"—";}
function seconds(value:number|undefined){return typeof value==="number"?`${value.toFixed(1)} s`:"—";}
type EvaluationTab="summary"|"conversation"|"character";

function characterTone(row:EvaluationRow){
  if(row.character_metrics?.break_turns)return "danger";
  if(row.character_metrics?.eligible)return "success";
  return row.character_status==="error"?"warning":"neutral";
}

export function EvaluationsView(){
  const [detail,setDetail]=useState<EvaluationRow|null>(null),[detailError,setDetailError]=useState("");
  const [detailTab,setDetailTab]=useState<EvaluationTab>("summary");
  const [horizon,setHorizon]=useState("");
  const rows=usePolling(()=>api<EvaluationRow[]>("/api/evaluations"),10000,[]);
  const filtered=(rows.data||[]).filter(row=>!horizon||String(row.character_target_turns)===horizon);
  async function open(row:EvaluationRow){
    try{setDetailError("");setDetailTab("summary");setDetail(await api<EvaluationRow>(`/api/evaluations/${row.id}`));}
    catch(reason){setDetailError(reason instanceof Error?reason.message:String(reason));}
  }
  const metrics=useMemo(()=>{
    const data=(rows.data||[]).filter(row=>!horizon||String(row.character_target_turns)===horizon);
    const verdictRows=data.filter(row=>typeof row.verdict_correct==="boolean");
    const correct=verdictRows.filter(row=>row.verdict_correct).length;
    const characterMetric=(status:string,label:string)=>{
      const eligible=data.filter(row=>row.character_status===status&&row.character_metrics?.eligible);
      const mixed=new Set(eligible.map(row=>row.character_target_turns)).size>1;
      const breaks=eligible.filter(row=>row.character_metrics?.session_break).length;
      return {label,value:mixed?"—":percentage(breaks,eligible.length),
        note:mixed?"Select one response-turn target":`${breaks}/${eligible.length} eligible runs · ${data.length-eligible.length} excluded`};
    };
    const indicators=data.reduce((total,row)=>total+Number(row.hvi_count||0),0);
    const verified=data.filter(row=>row.evidence_verified).length;
    return [
      {label:"Runs recorded",value:data.length,note:"Persisted synthetic executions"},
      {label:"Verdict accuracy",value:percentage(correct,verdictRows.length),note:`${correct}/${verdictRows.length} expected decisions`},
      {label:"Indicators / session",value:data.length?(indicators/data.length).toFixed(2):"—",note:"Extracted threat indicators"},
      characterMetric("automated","Automated break rate"),
      characterMetric("human_reviewed","Reviewed break rate"),
      {label:"Evidence verified",value:percentage(verified,data.length),note:`${verified}/${data.length} bundles passed`},
    ];
  },[rows.data,horizon]);
  if(rows.loading&&!rows.data)return <LoadingState label="Loading evaluation runs"/>;
  if(rows.error&&!rows.data)return <ErrorState message={rows.error} retry={()=>void rows.refresh()}/>;
  return <div className="view">
    <PageHeading eyebrow="Quality assurance" title="Evaluation runs" copy="Character consistency, indicator extraction, and evidence integrity under controlled testing." actions={<Chip tone="info">Synthetic corpus</Chip>}/>
    <label className="character-horizon">Response-turn target <select value={horizon} onChange={event=>setHorizon(event.target.value)}>
      <option value="">All targets</option>{[...new Set((rows.data||[]).flatMap(row=>row.character_target_turns?[row.character_target_turns]:[]))].sort((a,b)=>a-b).map(value=><option key={value} value={value}>{value} turns</option>)}
    </select></label>
    <div className="evaluation-metrics">{metrics.map(item=><article key={item.label}><span>{item.label}</span><strong>{item.value}</strong><p>{item.note}</p></article>)}</div>
    <Surface>{filtered.length?<div className="evaluation-matrix">
      <header><span>Scenario / run</span><span>Persona</span><span>Language</span><span>Decision</span><span>Score</span><span>F1</span><span>Character</span><span>Evidence</span></header>
      {filtered.map(row=><button key={row.id} onClick={()=>void open(row)}>
        <span><strong>{titleCase(row.scenario)}</strong><small>{row.id}</small></span>
        <span>{titleCase(row.persona)}</span><span>{row.language||"—"}</span>
        <Chip tone={verdictTone(row.verdict)}>{titleCase(row.verdict)}</Chip><Score value={row.verdict_score}/>
        <span>{typeof row.f1==="number"?row.f1.toFixed(2):"—"}</span>
        <Chip tone={characterTone(row)}>{characterLabel(row)}</Chip>
        <Chip tone={row.evidence_verified?"success":"warning"}>{row.evidence_verified?"Verified":"Check"}</Chip>
      </button>)}
    </div>:<EmptyState title="No evaluation runs" copy="Recorded evaluation packs appear here after the test runner completes."/>}</Surface>
    <p className="claim-limit">Automated scores are provisional, not human validation or real-scammer recognition. Session rates include only fully assessed runs reaching the same response-turn target. Unassessed, uncertain and incomplete runs are excluded—not passes. Human review moves a run into the reviewed group; the original automated assessment is retained.</p>
    {(detail||detailError)&&<Modal className="evaluation-modal" backdropClassName="evaluation-modal-backdrop" title={detail?`${titleCase(detail.scenario)} evaluation`:"Evaluation unavailable"} copy={detail?`${detail.id} · ${formatDate(detail.recorded_ts,true)}`:detailError} onClose={()=>{setDetail(null);setDetailError("");}} actions={detail?.package_download_url?<Button tone="primary" onClick={()=>window.open(authenticatedUrl(detail.package_download_url!),"_blank","noopener")}>Download evidence</Button>:undefined}>
      {detail&&<div className="evaluation-dialog">
        <nav className="evaluation-tabs" role="tablist" aria-label="Evaluation details">
          {([['summary','Summary'],['conversation','Conversation'],['character','Character review']] as const).map(([id,label])=><button key={id} id={`evaluation-tab-${id}`} role="tab" aria-selected={detailTab===id} aria-controls={`evaluation-panel-${id}`} className={detailTab===id?"active":""} onClick={()=>setDetailTab(id)}>{label}{id==="conversation"&&<span>{detail.transcript?.length||0}</span>}{id==="character"&&<Chip tone={characterTone(detail)}>{characterLabel(detail)}</Chip>}</button>)}
        </nav>
        <section className={`evaluation-tab-panel ${detailTab}`} id={`evaluation-panel-${detailTab}`} role="tabpanel" aria-labelledby={`evaluation-tab-${detailTab}`} tabIndex={0}>
          {detailTab==="summary"&&<div className="evaluation-summary">
            <header className="evaluation-assessment">
              <div><p className="eyebrow">Character consistency</p><div><h3>{characterLabel(detail)}</h3><Chip tone={characterTone(detail)}>{detail.character_status==="human_reviewed"?"Human reviewed":detail.character_status==="automated"?"Automated":"Pending review"}</Chip></div><p>{detail.character_assessment?.observed_turns??detail.character_response_turns?.length??0} delivered response turns · target {detail.character_target_turns??"unknown"} · {detail.character_metrics?.assessed_turns??0} assessed · {detail.character_metrics?.uncertain_turns??0} uncertain</p></div>
              <div className="evaluation-verdict"><span>Pipeline decision</span><Chip tone={verdictTone(detail.verdict)}>{titleCase(detail.verdict)}</Chip><Score value={detail.verdict_score}/></div>
            </header>
            <div className="evaluation-summary-grid">
              <article><span>Response break rate</span><strong>{typeof detail.character_metrics?.response_break_rate==="number"?`${(detail.character_metrics.response_break_rate*100).toFixed(1)}%`:"—"}</strong></article>
              <article><span>First break turn</span><strong>{detail.character_metrics?.first_break_turn??"—"}</strong></article>
              <article><span>Labelled-input F1</span><strong>{detail.f1?.toFixed(2)||"—"}</strong></article>
              <article><span>Planned reply latency</span><strong>{seconds(detail.mean_response_latency_s)}</strong></article>
              <article><span>Safety interventions</span><strong>{detail.outbound_guardrail_flags??"—"}</strong></article>
            </div>
            <p className="evaluation-note">Reply latency is the configured delivery wait, not observed engagement. Character scoring uses delivered replies after safeguards; adjacent HIVE bubbles count as one response turn.</p>
            <section className="evaluation-indicators"><header><div><h3>Extracted indicators</h3><p>{detail.hvi_items?.length||0} items recorded in this evaluation run.</p></div><Chip tone={detail.evidence_verified?"success":"warning"}>{detail.evidence_verified?"Evidence verified":"Evidence check needed"}</Chip></header><IndicatorList items={detail.hvi_items}/></section>
          </div>}
          {detailTab==="conversation"&&<div className="evaluation-conversation"><header><h3>Recorded conversation</h3><p>The transcript opens at the first message and follows the original message order.</p></header><Transcript initialScroll="start" messages={(detail.transcript||[]).map(([role,text],index)=>({role,text,msg_id:index}))}/></div>}
          {detailTab==="character"&&<CharacterReview key={detail.id} row={detail} onSaved={updated=>{setDetail(updated);void rows.refresh();}}/>}
        </section>
      </div>}
    </Modal>}
  </div>;
}
