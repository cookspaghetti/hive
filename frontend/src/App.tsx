import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { compareCasesNewestFirst } from "./caseOrder";
import { Button, Chip, Logo, Modal, titleCase } from "./components";
import { FirstRun } from "./FirstRun";
import type { ConfirmRequest, NoticeKind, NoticeRequest } from "./feedback";
import { usePolling } from "./hooks";
import { SetupDrawer } from "./SetupDrawer";
import type { CaseSummary, Route, RuntimeStatus } from "./types";
import { ActivityIcon, ArrowRightIcon, CasesIcon, ChevronLeftIcon, ChevronRightIcon, CloseIcon, ConsoleIcon, DemoIcon, EmptyIcon, EvaluationIcon, LogsIcon, MenuIcon, MoreIcon, SearchIcon, VaultIcon } from "./icons";
import { ActivityView } from "./views/Activity";
import { CaseWorkspace } from "./views/CaseWorkspace";
import { CasesView } from "./views/Cases";
import { ConsoleView } from "./views/Console";
import { DemoView } from "./views/Demo";
import { EvaluationsView } from "./views/Evaluations";
import { VaultView } from "./views/Vault";

const navigation = [["console","Console"],["cases","Cases"],["vault","Evidence vault"],["activity","Activity ledger"],["divider",""],["demo","Demo lab"],["evaluations","Evaluation runs"],["logs","Diagnostic logs"]] as const;
const navigationIcons = {console:ConsoleIcon,cases:CasesIcon,vault:VaultIcon,activity:ActivityIcon,demo:DemoIcon,evaluations:EvaluationIcon,logs:LogsIcon};
type Confirmation = ConfirmRequest & { resolve:(value:boolean)=>void };
type Toast = NoticeRequest & { id:number; kind:NoticeKind };

export function App() {
  const [route,setRoute]=useState<Route>(()=>parseRoute());
  const [setupOpen,setSetupOpen]=useState(false),[paletteOpen,setPaletteOpen]=useState(false),[mobileNav,setMobileNav]=useState(false);
  const [confirmation,setConfirmation]=useState<Confirmation|null>(null),[toasts,setToasts]=useState<Toast[]>([]),[firstRunDismissed,setFirstRunDismissed]=useState(false);
  const prompted=useRef(false);
  const runtime=usePolling(()=>api<RuntimeStatus>("/api/runtime/status"),5000,[]);
  const sessions=usePolling(()=>api<CaseSummary[]>("/api/sessions").catch(()=>[]),5000,[]);
  const history=usePolling(()=>api<CaseSummary[]>("/api/history").catch(()=>[]),10000,[]);
  const setup=usePolling(()=>api<Record<string,unknown>>("/api/setup/status").catch(()=>null),15000,[]);
  const navigate=useCallback((target:string)=>{window.location.hash=`#/${target}`;setMobileNav(false);},[]);
  useEffect(()=>{const handler=()=>setRoute(parseRoute());window.addEventListener("hashchange",handler);if(!window.location.hash)navigate("console");return()=>window.removeEventListener("hashchange",handler);},[navigate]);
  useEffect(()=>{const handler=(event:KeyboardEvent)=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="k"){event.preventDefault();setPaletteOpen(true);}if(event.key==="Escape"){setPaletteOpen(false);setMobileNav(false);}};window.addEventListener("keydown",handler);return()=>window.removeEventListener("keydown",handler);},[]);
  useEffect(()=>{const notice=(event:Event)=>{const request=(event as CustomEvent<NoticeRequest>).detail;const id=Date.now()+Math.random();const item:Toast={...request,id,kind:request.kind||"info"};setToasts(rows=>[...rows,item].slice(-3));if(item.kind!=="error")window.setTimeout(()=>setToasts(rows=>rows.filter(row=>row.id!==id)),6000);};const confirm=(event:Event)=>setConfirmation((event as CustomEvent<Confirmation>).detail);window.addEventListener("hive:notice",notice);window.addEventListener("hive:confirm",confirm);return()=>{window.removeEventListener("hive:notice",notice);window.removeEventListener("hive:confirm",confirm);};},[]);
  const checks=setup.data?.checks as Record<string,boolean>|undefined;
  const readiness=checks?`${Object.values(checks).filter(Boolean).length}/${Object.keys(checks).length}`:"—";
  const allReady=Boolean(checks&&Object.keys(checks).length&&Object.values(checks).every(Boolean));
  const noChecksReady=Boolean(checks&&Object.keys(checks).length&&Object.values(checks).every(value=>!value));
  const forceFirstRun=new URLSearchParams(window.location.search).get("first-run")==="1";
  const showFirstRun=!firstRunDismissed&&(forceFirstRun||noChecksReady);
  useEffect(()=>{if(!showFirstRun&&!prompted.current&&checks&&Object.values(checks).some(value=>!value)){prompted.current=true;setSetupOpen(true);}},[checks,showFirstRun]);
  if(showFirstRun)return <><FirstRun onComplete={()=>{setFirstRunDismissed(true);void setup.refresh();}}/><ToastStack rows={toasts} dismiss={id=>setToasts(rows=>rows.filter(row=>row.id!==id))}/></>;
  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav?"open":""}`}><header><Logo/><div><strong>HIVE</strong><span>Control panel</span></div><button className="icon-button mobile-only" title="Close navigation" aria-label="Close navigation" onClick={()=>setMobileNav(false)}><CloseIcon/></button></header><nav>{navigation.map(([name,label])=>{if(name==="divider")return <hr key={name}/>;const NavigationIcon=navigationIcons[name];return <div key={name}><button className={route.name===name?"active":""} onClick={()=>navigate(name)}><NavigationIcon/><span>{label}</span>{name==="cases"&&<b>{(sessions.data?.length||0)+(history.data?.length||0)}</b>}</button>{name==="cases"&&route.name==="case"&&<div className="case-rail">{sessions.data?.slice().sort(compareCasesNewestFirst).map(item=><button key={item.peer_id} className={String(item.peer_id)===route.id?"active":""} onClick={()=>navigate(`case/live/${item.peer_id}/conversation`)}><span>{item.peer_id}</span><strong>{typeof item.score==="number"?item.score.toFixed(2):"—"}</strong></button>)}</div>}</div>;})}</nav><button className={`setup-entry ${allReady?"healthy":"warning"}`} onClick={()=>setSetupOpen(true)}><span className={`status-dot ${allReady?"healthy":"warning"}`}/><span><strong>Setup</strong><small>Models · Telegram · Security</small></span><b>{readiness}</b></button></aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button mobile-only" title="Open navigation" aria-label="Open navigation" onClick={()=>setMobileNav(true)}><MenuIcon/></button><span className="mobile-brand mobile-only"><Logo/><strong>HIVE</strong></span><button className="breadcrumb" onClick={()=>route.name==="case"&&navigate("cases")}><span>{route.name==="case"?"Cases":"HIVE"}</span><b>/</b><strong>{route.name==="case"?route.id:titleCase(route.name)}</strong></button>{route.name==="case"&&route.source==="live"&&sessions.data?.length?<CaseStepper route={route} rows={sessions.data.slice().sort(compareCasesNewestFirst)} navigate={navigate}/>:null}<button className="command-trigger" onClick={()=>setPaletteOpen(true)}><SearchIcon size={15}/><span>Search or run an action</span><kbd>Ctrl K</kbd></button><Chip tone={runtime.data?.running?"success":runtime.data?.ready?"neutral":"warning"}><span className={`status-dot ${runtime.data?.running?"live":""}`}/>{runtime.data?.running?"Agent running":runtime.data?.ready?"Agent stopped":"Setup required"}</Chip></header><main id="workspace">{renderRoute(route,navigate,()=>setSetupOpen(true))}</main></div>
    <nav className="bottom-nav">{([["console","Console",ConsoleIcon],["cases","Cases",CasesIcon],["vault","Vault",VaultIcon]] as const).map(([name,label,NavigationIcon])=><button key={name} className={route.name===name?"active":""} onClick={()=>navigate(name)}><NavigationIcon/>{label}</button>)}<button onClick={()=>setMobileNav(true)}><MoreIcon/>More</button></nav>
    {mobileNav&&<button className="nav-scrim" aria-label="Close navigation" onClick={()=>setMobileNav(false)}/>}<SetupDrawer open={setupOpen} onClose={()=>setSetupOpen(false)}/>{paletteOpen&&<CommandPalette route={route} sessions={[...(sessions.data||[]),...(history.data||[])]} navigate={navigate} close={()=>setPaletteOpen(false)} openSetup={()=>{setPaletteOpen(false);setSetupOpen(true);}}/>}
    {confirmation&&<ConfirmationDialog request={confirmation} close={value=>{confirmation.resolve(value);setConfirmation(null);}}/>}<ToastStack rows={toasts} dismiss={id=>setToasts(rows=>rows.filter(row=>row.id!==id))}/>
  </div>;
}

function renderRoute(route:Route,navigate:(route:string)=>void,openSetup:()=>void){switch(route.name){case"cases":return <CasesView navigate={navigate}/>;case"case":return <CaseWorkspace source={route.source||"live"} id={route.id||""} navigate={navigate}/>;case"vault":return <VaultView/>;case"activity":return <ActivityView/>;case"logs":return <ActivityView logs/>;case"demo":return <DemoView/>;case"evaluations":return <EvaluationsView/>;default:return <ConsoleView navigate={navigate} openSetup={openSetup}/>;}}
function parseRoute():Route{const parts=window.location.hash.replace(/^#\/?/,"").split("/").filter(Boolean);if(parts[0]==="case")return{name:"case",source:parts[1]==="history"?"history":"live",id:parts[2]||""};const valid=["console","cases","vault","demo","evaluations","activity","logs"];return{name:(valid.includes(parts[0]||"")?parts[0]:"console") as Route["name"]};}
function CaseStepper({route,rows,navigate}:{route:Route;rows:CaseSummary[];navigate:(route:string)=>void}){const index=Math.max(0,rows.findIndex(row=>String(row.peer_id)===route.id));const tab=window.location.hash.split("/")[4]||"conversation";const move=(offset:number)=>{const next=rows[(index+offset+rows.length)%rows.length];if(next?.peer_id)navigate(`case/live/${next.peer_id}/${tab}`);};return <div className="case-stepper"><button title="Previous case" aria-label="Previous case" onClick={()=>move(-1)}><ChevronLeftIcon size={17}/></button><span>{index+1} of {rows.length}</span><button title="Next case" aria-label="Next case" onClick={()=>move(1)}><ChevronRightIcon size={17}/></button></div>;}

function CommandPalette({route,sessions,navigate,close,openSetup}:{route:Route;sessions:CaseSummary[];navigate:(route:string)=>void;close:()=>void;openSetup:()=>void}){
  const [query,setQuery]=useState(""),[selected,setSelected]=useState(0);
  const pages=navigation.filter(([key])=>key!=="divider").map(([key,label])=>({group:"Pages",title:label,detail:"Open page",run:()=>navigate(key)}));
  const cases=sessions.map(row=>({group:"Cases",title:row.name||`Peer ${row.peer_id}`,detail:`Case ${row.peer_id} · ${titleCase(row.verdict)}`,run:()=>navigate(`case/${row.id||row.history_id?"history":"live"}/${row.id||row.history_id||row.peer_id}/conversation`)}));
  const actions=[{group:"Actions",title:"Open setup",detail:"Review models, Telegram, security, and privacy",run:openSetup},{group:"Actions",title:"Review evidence vault",detail:"Inspect sealed bundles and checksums",run:()=>navigate("vault")},...(route.name==="case"&&route.source==="live"?[{group:"Actions",title:`Seal case ${route.id}`,detail:"Stop replying and create a signed evidence bundle",run:()=>window.dispatchEvent(new CustomEvent("hive:seal-current"))}]:[])];
  const results=useMemo(()=>[...actions,...cases,...pages].filter(item=>`${item.title} ${item.detail}`.toLowerCase().includes(query.toLowerCase())).slice(0,12),[query,sessions,route.name,route.id]);
  useEffect(()=>setSelected(0),[query]);const run=(index=selected)=>{const item=results[index];if(item){close();window.setTimeout(item.run,0);}};
  return <Modal title="Command palette" onClose={close}><label className="palette-search"><SearchIcon size={17}/><input value={query} onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{if(event.key==="ArrowDown"){event.preventDefault();setSelected(value=>Math.min(results.length-1,value+1));}if(event.key==="ArrowUp"){event.preventDefault();setSelected(value=>Math.max(0,value-1));}if(event.key==="Enter"){event.preventDefault();run();}}} placeholder="Search actions, cases, and pages"/><kbd>esc</kbd></label><div className="palette-results">{results.map((item,index)=><div className="palette-group" key={`${item.title}-${index}`}>{(index===0||results[index-1]?.group!==item.group)&&<p>{item.group}</p>}<button className={selected===index?"selected":""} onMouseEnter={()=>setSelected(index)} onClick={()=>run(index)}><span>{item.group==="Actions"?<ArrowRightIcon size={14}/>:<EmptyIcon size={14}/>}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div>{selected===index&&<b>↵</b>}</button></div>)}</div><footer className="palette-help">↑↓ move · ↵ run · destructive actions still confirm</footer></Modal>;
}
function ConfirmationDialog({request,close}:{request:Confirmation;close:(value:boolean)=>void}){return <Modal danger={request.danger!==false} title={request.title} copy={request.copy} onClose={()=>close(false)} actions={<><Button onClick={()=>close(false)}>{request.cancelLabel||"Cancel"}</Button><Button tone={request.danger===false?"primary":"danger"} onClick={()=>close(true)}>{request.confirmLabel}</Button></>}><p className="eyebrow">{request.eyebrow||"This action has consequences"}</p><div className="consequence-list">{request.consequences.map((item,index)=><article key={index}><span className={`status-dot ${item.tone||"warning"}`}/><div><strong>{item.title}</strong><p>{item.detail}</p></div></article>)}</div></Modal>}
function ToastStack({rows,dismiss}:{rows:Toast[];dismiss:(id:number)=>void}){return <aside className="toast-stack" aria-live="polite">{rows.map(row=><article className={`toast ${row.kind}`} key={row.id}><span className={`status-dot ${row.kind}`}/><div><strong>{row.title}</strong>{row.detail&&<p>{row.detail}</p>}</div><button title="Dismiss notification" aria-label="Dismiss notification" onClick={()=>dismiss(row.id)}><CloseIcon size={15}/></button></article>)}</aside>}
