import { FormEvent, useEffect, useId, useRef, useState } from "react";
import { api, post, put } from "./api";
import { Button, Chip, ErrorState, IconButton, LoadingState, titleCase } from "./components";
import { confirmAction, notify } from "./feedback";
import { ChevronDownIcon, CloseIcon } from "./icons";
import type { JsonRecord } from "./types";

const sections = ["models", "control_bot", "telethon", "signing_key", "intelligence", "panel", "privacy"] as const;
type Section = typeof sections[number];

export function SetupDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  onCloseRef.current = onClose;
  const [active, setActive] = useState<Section>("models");
  const [status, setStatus] = useState<JsonRecord | null>(null);
  const [form, setForm] = useState<Record<string,string>>({});
  const [attempt, setAttempt] = useState("");
  const [loginState, setLoginState] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setError("");
      const [nextStatus, models] = await Promise.all([api<JsonRecord>("/api/setup/status"), api<JsonRecord>("/api/models/status")]);
      setStatus(nextStatus);
      const modelMap = (models.models || {}) as JsonRecord;
      const paths = (nextStatus.paths || {}) as JsonRecord;
      setForm(previous => ({...previous,HIVE_LLM_BASE_URL:String(models.endpoint || ""),HIVE_LLM_MODEL_CHEAP:String(modelMap.cheap || ""),HIVE_LLM_MODEL_STRONG:String(modelMap.strong || ""),HIVE_LLM_MODEL_LIGHT:String(modelMap.light || ""),HIVE_VISION_MODEL:String(modelMap.vision || ""),session_path:String(paths.session || "./secrets/user.session"),signing_path:String(paths.signing_key || "./secrets/signing_key.pem")}));
      void api<JsonRecord>("/api/retention").then(retention => {const policy=(retention.policy||{}) as JsonRecord;setForm(previous=>({...previous,media_days:String(policy.media_days||30),demo_days:String(policy.demo_days||90),evaluation_days:String(policy.evaluation_days||180),active_checkpoint_review_days:String(policy.active_checkpoint_review_days||7)}));}).catch(()=>undefined);
    } catch (reason) { setError(message(reason)); }
  }
  useEffect(() => { if (open) void refresh(); }, [open]);
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const drawer = drawerRef.current;
    const focusable = () => Array.from(drawer?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || []).filter(element => !element.hidden && element.getAttribute("aria-hidden") !== "true");
    window.requestAnimationFrame(() => (focusable()[0] || drawer)?.focus());
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onCloseRef.current(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) { event.preventDefault(); drawer?.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown",handler);
    return () => { document.removeEventListener("keydown",handler); previousFocus?.focus(); };
  }, [open]);
  if (!open) return null;
  const checks=(status?.checks || {}) as Record<string,boolean>;
  const readyCount=Object.values(checks).filter(Boolean).length;
  const total=Object.keys(checks).length;
  async function run(task:()=>Promise<void>){setBusy(true);setError("");setNotice("");try{await task();}catch(reason){const detail=message(reason);setError(detail);notify({title:"Setup action failed",detail,kind:"error"});}finally{setBusy(false);}}
  async function saveModels(event:FormEvent){event.preventDefault();await run(async()=>{const payload=Object.fromEntries(Object.entries(form).filter(([key,value])=>key.startsWith("HIVE_")&&value.trim()));await put("/api/setup/config",payload);await post("/api/runtime/restart",{force:false});setNotice("Model routing saved and the agent restarted.");await refresh();});}
  async function probeModels(){await run(async()=>{const result=await post<JsonRecord>("/api/models/probe");setNotice(String(result.detail || "Model endpoint check passed."));});}
  async function verifyBot(event:FormEvent){event.preventDefault();await run(async()=>{const result=await post<JsonRecord>("/api/setup/bot/verify",{token:form.bot_token||"",operator_id:form.operator_id||"",operator_name:form.operator_name||""});const bot=(result.bot||{}) as JsonRecord;setNotice(`Control bot verified${bot.username?` as @${bot.username}`:""}.`);await refresh();});}
  async function startLogin(event:FormEvent){event.preventDefault();await run(async()=>{const result=await post<JsonRecord>("/api/setup/telethon/start",{api_id:Number(form.api_id||0),api_hash:form.api_hash||"",phone:form.phone||"",passphrase:form.passphrase||"",session_path:form.session_path||"./secrets/user.session"});setAttempt(String(result.attempt_id||""));setLoginState(String(result.state||"code_required"));setNotice("Telegram sent a login code to the account.");});}
  async function submitCode(event:FormEvent){event.preventDefault();await run(async()=>{const result=await post<JsonRecord>("/api/setup/telethon/code",{attempt_id:attempt,code:form.login_code||""});setLoginState(String(result.state||""));setNotice(result.state==="ready"?"Telegram data plane authorised.":"Two-step verification password required.");if(result.state==="ready")await refresh();});}
  async function submitPassword(event:FormEvent){event.preventDefault();await run(async()=>{await post("/api/setup/telethon/password",{attempt_id:attempt,password:form.tg_password||""});setLoginState("ready");setNotice("Telegram data plane authorised.");await refresh();});}
  async function createKey(){await run(async()=>{await post("/api/setup/signing-key",{path:form.signing_path||""});setNotice("Evidence signing key is ready.");await refresh();});}
  async function rotateKey(){const key=(status?.signing_key||{}) as JsonRecord;const fingerprint=String(key.fingerprint||"");if(!fingerprint||!await confirmAction({title:`Rotate signing key ${fingerprint}?`,copy:"The active key changes for future evidence packages.",confirmLabel:"Rotate signing key",consequences:[{tone:"warning",title:"The previous key is retained",detail:"Existing sealed bundles remain verifiable with their original public key."},{tone:"error",title:"The agent must be stopped",detail:"Rotation is rejected while active takeovers could still seal."},{tone:"info",title:"A restart applies the new key",detail:"Do not seal new evidence until readiness reports the new fingerprint."}]}))return;await run(async()=>{await post("/api/setup/signing-key/rotate",{confirm_fingerprint:fingerprint,reason:"operator initiated from setup drawer"});setNotice("Signing key rotated. Restart the agent before sealing new evidence.");notify({title:"Signing key rotated",detail:"Restart the agent before sealing new evidence.",kind:"success"});await refresh();});}
  async function savePanelToken(event:FormEvent){event.preventDefault();await run(async()=>{if(!form.panel_token?.trim())throw new Error("Enter a replacement panel token.");await put("/api/setup/config",{HIVE_PANEL_TOKEN:form.panel_token.trim()});setNotice("Panel token saved. Refresh after the runtime reloads configuration.");});}
  async function saveIntelligence(event:FormEvent){event.preventDefault();await run(async()=>{const keys=["HIVE_VIRUSTOTAL_API_KEY","HIVE_ABUSEIPDB_API_KEY"] as const;const payload=Object.fromEntries(keys.map(key=>[key,(form[key]||"").trim()]).filter(([,value])=>value));if(!Object.keys(payload).length)throw new Error("Enter at least one provider key.");await put("/api/setup/config",payload);await post("/api/runtime/restart",{force:false});setForm(previous=>({...previous,HIVE_VIRUSTOTAL_API_KEY:"",HIVE_ABUSEIPDB_API_KEY:""}));setNotice("Threat-intelligence credentials saved and the agent restarted.");await refresh();});}
  async function saveRetention(event:FormEvent){event.preventDefault();await run(async()=>{await put("/api/retention/policy",{media_days:Number(form.media_days||30),demo_days:Number(form.demo_days||90),evaluation_days:Number(form.evaluation_days||180),active_checkpoint_review_days:Number(form.active_checkpoint_review_days||7)});setNotice("Retention policy saved. This report-only policy does not delete artifacts.");});}

  return <div className="drawer-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}><aside ref={drawerRef} className="setup-drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}><header><div><p className="eyebrow">Readiness</p><h2 id={titleId}>{total&&readyCount===total?"All checks passing":`${readyCount} of ${total||5} checks passing`}</h2><p>Expand a readiness check to review or update its configuration.</p></div><IconButton label="Close setup" onClick={onClose}><CloseIcon/></IconButton></header><nav aria-label="Readiness checks">{sections.map(section=><button key={section} className={active===section?"active":""} aria-expanded={active===section} aria-controls="setup-check-detail" onClick={()=>setActive(section)}><span className={`status-dot ${sectionReady(section,checks)?"healthy":"warning"}`}/><span><strong>{titleCase(section)}</strong><small>{sectionCopy(section)}</small></span><Chip tone={sectionReady(section,checks)?"success":"warning"}>{sectionReady(section,checks)?"Ready":"Review"}</Chip><b className={active===section?"expanded":""} aria-hidden="true"><ChevronDownIcon size={14}/></b></button>)}</nav><div className="drawer-content" id="setup-check-detail">{!status&&!error&&<LoadingState label="Checking setup"/>}{error&&<ErrorState message={error}/>} {notice&&<div className="inline-notice">{notice}</div>}
    {status&&active==="models"&&<form onSubmit={saveModels}><h3>Model routing</h3><p>Assign each task to the configured compatible model endpoint.</p><div className="form-grid"><Field label="API base URL" name="HIVE_LLM_BASE_URL" form={form} setForm={setForm}/><Field label="New API key (optional)" name="HIVE_LLM_API_KEY" type="password" form={form} setForm={setForm}/><Field label="Cheap model" name="HIVE_LLM_MODEL_CHEAP" form={form} setForm={setForm}/><Field label="Strong model" name="HIVE_LLM_MODEL_STRONG" form={form} setForm={setForm}/><Field label="Verdict model" name="HIVE_LLM_MODEL_LIGHT" form={form} setForm={setForm}/><Field label="Vision model" name="HIVE_VISION_MODEL" form={form} setForm={setForm}/></div><div className="button-stack"><Button tone="primary" disabled={busy} type="submit">Save and restart agent</Button><Button disabled={busy} onClick={()=>void probeModels()}>Test endpoint</Button></div></form>}
    {status&&active==="control_bot"&&<form onSubmit={verifyBot}><h3>Control bot</h3><p>Verify the bot used for operator approvals and evidence delivery.</p><div className="form-grid"><Field label="Bot token" name="bot_token" type="password" form={form} setForm={setForm}/><Field label="Operator ID" name="operator_id" form={form} setForm={setForm}/><Field label="Operator name" name="operator_name" form={form} setForm={setForm}/></div><Button tone="primary" disabled={busy} type="submit">Verify control bot</Button></form>}
    {status&&active==="telethon"&&<div className="setup-stack"><form onSubmit={startLogin}><h3>Telegram data plane</h3><p>Authorise the approved research account. The encryption passphrase has no recovery path.</p><div className="form-grid"><Field label="API ID" name="api_id" form={form} setForm={setForm}/><Field label="API hash" name="api_hash" type="password" form={form} setForm={setForm}/><Field label="Phone" name="phone" form={form} setForm={setForm}/><Field label="Encryption passphrase" name="passphrase" type="password" form={form} setForm={setForm}/><Field label="Session path" name="session_path" form={form} setForm={setForm}/></div><Button tone="primary" disabled={busy} type="submit">Send login code</Button></form>{attempt&&loginState!=="ready"&&<form onSubmit={loginState==="password_required"?submitPassword:submitCode}><h3>{loginState==="password_required"?"Two-step verification":"Login code"}</h3><Field label={loginState==="password_required"?"Telegram password":"Code"} name={loginState==="password_required"?"tg_password":"login_code"} type={loginState==="password_required"?"password":"text"} form={form} setForm={setForm}/><Button tone="primary" disabled={busy} type="submit">Continue</Button></form>}</div>}
    {status&&active==="signing_key"&&<div className="setup-stack"><div><h3>Evidence signing</h3><p>Signing proves sealed bytes have not changed; it does not prove sender identity.</p><Field label="Signing key path" name="signing_path" form={form} setForm={setForm}/><div className="button-stack"><Button tone="primary" disabled={busy} onClick={()=>void createKey()}>Create or verify key</Button><Button tone="danger" disabled={busy||!Boolean((status.signing_key as JsonRecord)?.rotation_allowed)} onClick={()=>void rotateKey()}>Rotate key</Button></div></div></div>}
    {status&&active==="intelligence"&&<form onSubmit={saveIntelligence}><h3>Threat intelligence</h3><p>Semak Mule and RDAP work without keys. Optional provider credentials stay local and are queried only for extracted observables.</p><ProviderReadiness status={(status.threat_intelligence||{}) as JsonRecord}/><div className="form-grid"><Field label="New VirusTotal key" name="HIVE_VIRUSTOTAL_API_KEY" type="password" form={form} setForm={setForm}/><Field label="New AbuseIPDB key" name="HIVE_ABUSEIPDB_API_KEY" type="password" form={form} setForm={setForm}/></div><Button tone="primary" disabled={busy} type="submit">Save and restart agent</Button><p className="claim-limit">Unknown APKs are never uploaded automatically. HIVE performs VirusTotal hash lookup only.</p></form>}
    {status&&active==="panel"&&<form onSubmit={savePanelToken}><h3>Panel access</h3><p>Changing this token invalidates the browser session after configuration reload.</p><Field label="Replacement panel token" name="panel_token" type="password" form={form} setForm={setForm}/><Button tone="primary" disabled={busy} type="submit">Save panel token</Button></form>}
    {status&&active==="privacy"&&<form onSubmit={saveRetention}><h3>Retention policy</h3><p>Choose review thresholds for locally held artifacts. Saving never deletes anything.</p><div className="form-grid"><Field label="Captured media days" name="media_days" type="number" form={form} setForm={setForm}/><Field label="Demo days" name="demo_days" type="number" form={form} setForm={setForm}/><Field label="Evaluation days" name="evaluation_days" type="number" form={form} setForm={setForm}/><Field label="Active checkpoint review days" name="active_checkpoint_review_days" type="number" form={form} setForm={setForm}/></div><Button tone="primary" disabled={busy} type="submit">Save retention</Button></form>}
  </div></aside></div>;
}

function Field({label,name,type="text",form,setForm}:{label:string;name:string;type?:string;form:Record<string,string>;setForm:(value:Record<string,string>)=>void}){return <label className="field"><span>{label}</span><input type={type} value={form[name]||""} onChange={event=>setForm({...form,[name]:event.target.value})}/></label>;}
function ProviderReadiness({status}:{status:JsonRecord}){const providers=[["semak_mule","Semak Mule"],["virus_total","VirusTotal"],["abuse_ipdb","AbuseIPDB"],["rdap","RDAP"]] as const;return <div className="provider-readiness">{providers.map(([key,label])=><span key={key}><i className={`status-dot ${status[key]?"healthy":"warning"}`}/>{label}<small>{status[key]?"Ready":"Key required"}</small></span>)}</div>}
function sectionReady(section:Section,checks:Record<string,boolean>){if(section==="privacy")return true;return Boolean(checks[section==="models"?"llm":section]);}
function sectionCopy(section:Section){return{models:"Routing and connectivity",control_bot:"Operator approvals",telethon:"Telegram data plane",signing_key:"Evidence signatures",intelligence:"Keyless core · optional providers",panel:"Authenticated access",privacy:"Retention on this machine"}[section];}
function message(reason:unknown){return reason instanceof Error?reason.message:String(reason);}
