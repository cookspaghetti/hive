import { useState } from "react";
import { api } from "../api";
import { Button,Chip,titleCase } from "../components";
import type { CharacterTurnScore,EvaluationRow } from "../types";

export function characterLabel(row:EvaluationRow){
  if(!row.character_status||row.character_status==="not_assessed")return "Not assessed";
  if(row.character_status==="error")return "Review needed";
  const prefix=row.character_status==="human_reviewed"?"Reviewed":"Auto";
  if(row.character_metrics?.break_turns)return `${prefix}: break`;
  return `${prefix}: ${row.character_metrics?.eligible?"no break":"incomplete"}`;
}

export function CharacterReview({row,onSaved}:{row:EvaluationRow;onSaved:(row:EvaluationRow)=>void}){
  const [editing,setEditing]=useState(false),[reviewer,setReviewer]=useState(""),[note,setNote]=useState("");
  const [scores,setScores]=useState<Record<number,CharacterTurnScore>>({}),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const assessment=row.character_assessment,turns=row.character_response_turns||[];
  const categories=row.character_rubric?.categories||{};
  const update=(turn:number,changes:Partial<CharacterTurnScore>)=>setScores(current=>({...current,[turn]:{turn,verdict:"uncertain",reason:"",findings:[],...current[turn],...changes}}));
  const canSave=turns.length>0&&turns.every(turn=>{
    const score=scores[turn.turn];
    return score&&score.reason.trim().length>=3&&(score.verdict!=="break"||score.findings.some(f=>f.category&&f.reason.trim().length>=3&&f.quote.trim()&&turn.messages.some(message=>message.includes(f.quote))));
  })&&reviewer.trim().length>=2&&note.trim().length>=3;
  async function save(){
    if(!canSave||busy)return;
    setBusy(true);setError("");
    try{
      const updated=await api<EvaluationRow>(`/api/evaluations/${row.id}/character-review`,{method:"POST",body:JSON.stringify({reviewer,note,source_sha256:assessment?.source_sha256,previous_review_id:row.character_review?.id||null,turns:turns.map(turn=>scores[turn.turn])})});
      onSaved(updated);setEditing(false);setScores({});
    }catch(reason){setError(reason instanceof Error?reason.message:String(reason));}finally{setBusy(false);}
  }
  return <section className="character-review">
    <header><h3>Character consistency</h3><Chip tone="info">{assessment?.rubric_version||"Not assessed"}</Chip></header>
    <p>{assessment?.observed_turns??turns.length} delivered response turns · target {assessment?.target_turns??"unknown"} · {row.character_metrics?.assessed_turns??0} assessed · {row.character_metrics?.uncertain_turns??0} uncertain.</p>
    {assessment?.error&&<p role="alert">{assessment.error}</p>}
    {!row.character_metrics?.eligible&&<p className="claim-limit">Not eligible for the full-session rate. Complete the review and reach the recorded target; an unknown or short horizon is never treated as break-free completion.</p>}
    <details><summary>Persona and scoring rubric</summary><pre>{row.character_persona}</pre>{Object.entries(categories).map(([key,value])=><p key={key}><strong>{titleCase(key)}:</strong> {value}</p>)}<p>Assess HIVE's own statements, not scammer accusations. Language switching, safe refusals and natural emotional changes are not automatically breaks. Mark ambiguous cases uncertain.</p></details>
    {row.character_review&&<p>Reviewed by {row.character_review.reviewer} · {row.character_review.reviewed_utc}<br/>{row.character_review.note}</p>}
    {!!assessment?.turns.length&&<details><summary>Recorded turn assessments</summary>{assessment.turns.map(score=><article className="character-finding" key={score.turn}><strong>Turn {score.turn} · {titleCase(score.verdict)}</strong><p>{score.reason}</p>{score.findings.map((finding,index)=><div key={index}><Chip tone="warning">{titleCase(finding.category)}</Chip><blockquote>{finding.quote}</blockquote><p>{finding.reason}</p></div>)}</article>)}</details>}
    {row.character_status==="human_reviewed"&&row.character_automated_assessment?.status==="automated"&&<details><summary>Original automated assessment (provisional)</summary><pre>{JSON.stringify(row.character_automated_assessment.turns,null,2)}</pre></details>}
    {!editing?<Button disabled={!turns.length||!assessment} onClick={()=>{setEditing(true);setScores({});}}>Review character</Button>:<>
      <p className="claim-limit">Review every turn explicitly. Each save creates a new revision; original runs and signed evidence are unchanged. This records an operator review, not an independent blinded study.</p>
      <label>Reviewer name<input value={reviewer} maxLength={120} onChange={event=>setReviewer(event.target.value)}/></label>
      <label>Review note<textarea value={note} maxLength={2000} onChange={event=>setNote(event.target.value)}/></label>
      {turns.map(turn=>{
        const score=scores[turn.turn],finding=score?.findings[0];
        const setFinding=(changes:Partial<{category:string;quote:string;reason:string}>)=>update(turn.turn,{findings:[{category:"",quote:"",reason:"",...finding,...changes}]});
        return <article className="character-finding" key={turn.turn}>
          <strong>HIVE response turn {turn.turn}</strong>{turn.messages.map((text,index)=><blockquote key={index}>{text}</blockquote>)}
          <label>Assessment for turn {turn.turn}<select value={score?.verdict||""} onChange={event=>{
            const verdict=event.target.value as CharacterTurnScore["verdict"];
            update(turn.turn,{verdict,reason:verdict==="pass"?"Consistent with the assigned persona and prior conversation.":verdict==="uncertain"?"Insufficient evidence for a confident assessment.":"",findings:verdict==="break"?[{category:"",quote:"",reason:""}]:[]});
          }}><option value="" disabled>Choose an assessment</option><option value="pass">No observed break</option><option value="break">Character break</option><option value="uncertain">Uncertain</option></select></label>
          {score&&<label>Reason for turn {turn.turn}<textarea maxLength={2000} value={score.reason} onChange={event=>update(turn.turn,{reason:event.target.value})}/></label>}
          {score?.verdict==="break"&&<>
            <label>Failure category<select value={finding?.category||""} onChange={event=>setFinding({category:event.target.value})}><option value="" disabled>Choose a category</option>{Object.keys(categories).map(key=><option key={key} value={key}>{titleCase(key)}</option>)}</select></label>
            <label>Exact quote from this response<textarea value={finding?.quote||""} onChange={event=>setFinding({quote:event.target.value})}/></label>
            <label>Evidence explanation<textarea maxLength={2000} value={finding?.reason||""} onChange={event=>setFinding({reason:event.target.value})}/></label>
          </>}
        </article>;
      })}
      {error&&<p role="alert">{error}</p>}
      <div className="button-stack"><Button disabled={busy} onClick={()=>setEditing(false)}>Cancel review</Button><Button tone="primary" disabled={!canSave||busy} onClick={()=>void save()}>{busy?"Saving…":"Save character review"}</Button></div>
    </>}
  </section>;
}
