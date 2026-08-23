import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { Button, Chip, Logo, Modal, titleCase } from "./components";
import { usePolling } from "./hooks";
import { SetupDrawer } from "./SetupDrawer";
import type { CaseSummary, Route, RuntimeStatus } from "./types";
import { ActivityView } from "./views/Activity";
import { CaseWorkspace } from "./views/CaseWorkspace";
import { CasesView } from "./views/Cases";
import { ConsoleView } from "./views/Console";
import { DemoView } from "./views/Demo";
import { EvaluationsView } from "./views/Evaluations";
import { VaultView } from "./views/Vault";

const navigation = [
  ["console","Console"], ["cases","Cases"], ["vault","Evidence vault"], ["activity","Activity ledger"],
  ["divider",""], ["demo","Demo lab"], ["evaluations","Evaluation runs"], ["logs","Diagnostic logs"],
] as const;

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const [setupOpen, setSetupOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const runtime = usePolling(() => api<RuntimeStatus>("/api/runtime/status"), 5000, []);
  const sessions = usePolling(() => api<CaseSummary[]>("/api/sessions").catch(() => []), 5000, []);
  const setup = usePolling(() => api<Record<string,unknown>>("/api/setup/status").catch(() => null), 15000, []);
  const navigate = useCallback((target: string) => { window.location.hash = `#/${target}`; setMobileNav(false); }, []);
  useEffect(() => { const handler = () => setRoute(parseRoute()); window.addEventListener("hashchange", handler); if (!window.location.hash) navigate("console"); return () => window.removeEventListener("hashchange", handler); }, [navigate]);
  useEffect(() => { const handler = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setPaletteOpen(true); } if (event.key === "Escape") { setPaletteOpen(false); setMobileNav(false); } }; window.addEventListener("keydown", handler); return () => window.removeEventListener("keydown", handler); }, []);
  const checks = setup.data?.checks as Record<string,boolean> | undefined;
  const readiness = checks ? `${Object.values(checks).filter(Boolean).length}/${Object.keys(checks).length}` : "—";
  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav ? "open" : ""}`}><header><Logo/><div><strong>HIVE</strong><span>Control panel</span></div><button className="icon-button mobile-only" aria-label="Close navigation" onClick={() => setMobileNav(false)}>×</button></header><nav>{navigation.map(([name,label]) => name === "divider" ? <hr key={name}/> : <div key={name}><button className={route.name === name ? "active" : ""} onClick={() => navigate(name)}><i/><span>{label}</span>{name === "cases" && <b>{sessions.data?.length || 0}</b>}</button>{name === "cases" && route.name === "case" && <div className="case-rail">{sessions.data?.map(item => <button key={item.peer_id} className={String(item.peer_id) === route.id ? "active" : ""} onClick={() => navigate(`case/live/${item.peer_id}`)}><span>{item.peer_id}</span><strong>{typeof item.score === "number" ? item.score.toFixed(2) : "—"}</strong></button>)}</div>}</div>)}</nav><button className={`setup-entry ${checks && Object.values(checks).every(Boolean) ? "healthy" : "warning"}`} onClick={() => setSetupOpen(true)}><span className="status-dot"/><span><strong>Setup</strong><small>Models · Telegram · Security</small></span><b>{readiness}</b></button></aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button mobile-only" aria-label="Open navigation" onClick={() => setMobileNav(true)}>≡</button><div className="breadcrumb"><span>{route.name === "case" ? "Cases" : "HIVE"}</span><b>/</b><strong>{route.name === "case" ? route.id : titleCase(route.name)}</strong></div>{route.name === "case" && sessions.data?.length ? <CaseStepper route={route} rows={sessions.data} navigate={navigate}/> : null}<button className="command-trigger" onClick={() => setPaletteOpen(true)}><span>Search or run an action</span><kbd>Ctrl K</kbd></button><Chip tone={runtime.data?.running ? "success" : runtime.data?.ready ? "neutral" : "warning"}><span className={`status-dot ${runtime.data?.running ? "live" : ""}`}/>{runtime.data?.running ? "Agent running" : runtime.data?.ready ? "Agent stopped" : "Setup required"}</Chip></header><main id="workspace">{renderRoute(route, navigate, () => setSetupOpen(true))}</main></div>
    <nav className="bottom-nav">{([ ["console","Console"], ["cases","Cases"], ["vault","Vault"] ] as const).map(([name,label]) => <button key={name} className={route.name === name ? "active" : ""} onClick={() => navigate(name)}><span>{name === "console" ? "◇" : name === "cases" ? "≡" : "□"}</span>{label}</button>)}<button onClick={() => setMobileNav(true)}><span>•••</span>More</button></nav>
    {mobileNav && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)}/>}<SetupDrawer open={setupOpen} onClose={() => setSetupOpen(false)}/>{paletteOpen && <CommandPalette sessions={sessions.data || []} navigate={navigate} close={() => setPaletteOpen(false)} openSetup={() => {setPaletteOpen(false);setSetupOpen(true);}}/>}
  </div>;
}

function renderRoute(route: Route, navigate: (route:string)=>void, openSetup:()=>void) { switch(route.name) { case "cases": return <CasesView navigate={navigate}/>; case "case": return <CaseWorkspace source={route.source || "live"} id={route.id || ""} navigate={navigate}/>; case "vault": return <VaultView/>; case "activity": return <ActivityView/>; case "logs": return <ActivityView logs/>; case "demo": return <DemoView/>; case "evaluations": return <EvaluationsView/>; default: return <ConsoleView navigate={navigate} openSetup={openSetup}/>; } }
function parseRoute(): Route { const parts = window.location.hash.replace(/^#\/?/,"").split("/").filter(Boolean); if (parts[0] === "case") return {name:"case",source:parts[1] === "history" ? "history" : "live",id:parts[2] || ""}; const valid = ["console","cases","vault","demo","evaluations","activity","logs"]; return {name:(valid.includes(parts[0] || "") ? parts[0] : "console") as Route["name"]}; }
function CaseStepper({route,rows,navigate}:{route:Route;rows:CaseSummary[];navigate:(route:string)=>void}) { const index = Math.max(0,rows.findIndex(row => String(row.peer_id) === route.id)); const move = (offset:number) => { const next = rows[(index + offset + rows.length) % rows.length]; if(next?.peer_id) navigate(`case/live/${next.peer_id}`); }; return <div className="case-stepper"><button onClick={() => move(-1)}>‹</button><span>{index + 1} of {rows.length}</span><button onClick={() => move(1)}>›</button></div>; }
function CommandPalette({sessions,navigate,close,openSetup}:{sessions:CaseSummary[];navigate:(route:string)=>void;close:()=>void;openSetup:()=>void}) { const [query,setQuery] = useState(""); const pages = navigation.filter(([key]) => key !== "divider").map(([key,label]) => ({title:label,detail:"Open page",run:()=>navigate(key)})); const cases = sessions.map(row => ({title:row.name || `Peer ${row.peer_id}`,detail:`Case ${row.peer_id} · ${titleCase(row.verdict)}`,run:()=>navigate(`case/live/${row.peer_id}`)})); const actions = [{title:"Open setup",detail:"Review models, Telegram, security, and privacy",run:openSetup},{title:"Review evidence vault",detail:"Inspect sealed bundles and checksums",run:()=>navigate("vault")}]; const results = useMemo(() => [...actions,...cases,...pages].filter(item => `${item.title} ${item.detail}`.toLowerCase().includes(query.toLowerCase())).slice(0,10), [query,sessions]); return <Modal title="Command palette" onClose={close}><label className="palette-search"><span>&gt;</span><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Search actions, cases, and pages"/><kbd>esc</kbd></label><div className="palette-results">{results.map((item,index) => <button key={`${item.title}-${index}`} onClick={() => {item.run();close();}}><span>{index < actions.length ? "→" : "◇"}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div><b>↵</b></button>)}</div><footer className="palette-help">↑↓ move · ↵ run · destructive actions still confirm</footer></Modal>; }
