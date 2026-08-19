# ruff: noqa: E501
"""Standalone localhost credential setup panel."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from hive.audit import audit_event
from hive.provisioning import EnvStore, TelethonLoginManager
from hive.provisioning.telegram_bot import verify_control_bot_token
from hive.vault.signer import generate_keypair
from hive.webpanel.assets import (
    LOGO_HTML,
    LOGO_PATH,
    PANEL_CSS_PATH,
    PANEL_JS_PATH,
    PANEL_TEMPLATE_PATH,
)

CONFIG_KEYS = {
    "HF_TOKEN",
    "HIVE_LLM_API_KEY",
    "HIVE_LLM_BASE_URL",
    "HIVE_LLM_MODEL_CHEAP",
    "HIVE_LLM_MODEL_LIGHT",
    "HIVE_LLM_MODEL_STRONG",
    "HIVE_VISION_MODEL",
    "HIVE_PANEL_TOKEN",
}


def create_setup_app(
    *,
    setup_token: str,
    root: str | Path = ".",
    env_store: EnvStore | None = None,
    login_manager: TelethonLoginManager | None = None,
) -> FastAPI:
    if not setup_token:
        raise ValueError("a setup token is required")

    project_root = Path(root).resolve()
    store = env_store or EnvStore(project_root / ".env")
    manager = login_manager or TelethonLoginManager(store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await manager.close()

    app = FastAPI(title="HIVE Setup", docs_url=None, redoc_url=None, lifespan=lifespan)

    def auth(
        x_hive_token: str = Header(default=""),
        x_hive_setup_token: str = Header(default=""),
    ) -> None:
        if (x_hive_token or x_hive_setup_token) != setup_token:
            raise HTTPException(status_code=401, detail="unauthorised")

    register_setup_routes(
        app,
        auth=auth,
        root=project_root,
        env_store=store,
        login_manager=manager,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(
            panel_page(setup_token),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/logo.png")
    @app.get("/favicon.png")
    def logo() -> FileResponse:
        return FileResponse(LOGO_PATH, media_type="image/png")

    @app.get("/panel.css")
    def panel_css() -> FileResponse:
        return FileResponse(
            PANEL_CSS_PATH,
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/panel.js")
    def panel_js() -> FileResponse:
        return FileResponse(
            PANEL_JS_PATH,
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    return app


def register_setup_routes(
    app: FastAPI,
    *,
    auth: Any,
    root: str | Path = ".",
    env_store: EnvStore | None = None,
    login_manager: TelethonLoginManager | None = None,
    on_change: Any = None,
) -> TelethonLoginManager:
    project_root = Path(root).resolve()
    store = env_store or EnvStore(project_root / ".env")
    manager = login_manager or TelethonLoginManager(store)

    def changed() -> None:
        if on_change is not None:
            on_change()

    @app.get("/api/setup/status", dependencies=[Depends(auth)])
    def status() -> dict[str, object]:
        values = store.read()
        session_path = _safe_path(
            project_root, values.get("HIVE_TG_SESSION_PATH", "./secrets/user.session")
        )
        signing_path = _safe_path(
            project_root, values.get("HIVE_SIGNING_KEY_PATH", "./secrets/signing_key.pem")
        )
        checks = {
            "llm": bool(values.get("HIVE_LLM_API_KEY")),
            "control_bot": bool(
                values.get("HIVE_CONTROL_BOT_TOKEN")
                and _positive_int(values.get("HIVE_OPERATOR_ID"))
            ),
            "telethon": bool(
                values.get("HIVE_TG_API_ID")
                and values.get("HIVE_TG_API_HASH")
                and values.get("HIVE_TG_PHONE")
                and values.get("HIVE_SESSION_PASSPHRASE")
                and session_path.is_file()
            ),
            "signing_key": signing_path.is_file(),
            "panel": bool(values.get("HIVE_PANEL_TOKEN")),
        }
        return {
            "ready": all(
                checks[name] for name in ("llm", "control_bot", "telethon", "signing_key")
            ),
            "checks": checks,
            "bot_username": values.get("HIVE_CONTROL_BOT_USERNAME", ""),
            "paths": {
                "session": _display_path(project_root, session_path),
                "signing_key": _display_path(project_root, signing_path),
            },
        }

    @app.put("/api/setup/config", dependencies=[Depends(auth)])
    def save_config(payload: Annotated[dict, Body()]) -> dict[str, bool]:
        unknown = set(payload) - CONFIG_KEYS
        if unknown:
            detail = f"unsupported setting: {sorted(unknown)[0]}"
            raise HTTPException(status_code=400, detail=detail)
        try:
            store.save(payload)
            if "HF_TOKEN" in payload:
                if payload["HF_TOKEN"]:
                    os.environ["HF_TOKEN"] = str(payload["HF_TOKEN"])
                else:
                    os.environ.pop("HF_TOKEN", None)
            changed()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(
            "configuration",
            "configuration_saved",
            component="webpanel.setup",
            payload={"keys": sorted(payload)},
        )
        return {"ok": True}

    @app.post("/api/setup/bot/verify", dependencies=[Depends(auth)])
    async def verify_bot(payload: Annotated[dict, Body()]) -> dict[str, object]:
        token = str(payload.get("token", "")).strip()
        operator_id = _positive_int(payload.get("operator_id"))
        operator_name = str(payload.get("operator_name", "")).strip()
        if not operator_id:
            raise HTTPException(status_code=400, detail="operator ID must be a positive integer")
        try:
            bot = await verify_control_bot_token(token)
            store.save(
                {
                    "HIVE_CONTROL_BOT_TOKEN": token,
                    "HIVE_OPERATOR_ID": operator_id,
                    "HIVE_OPERATOR_NAME": operator_name,
                }
            )
            changed()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(
            "configuration",
            "control_bot_verified",
            component="webpanel.setup",
            payload={
                "operator_id": operator_id,
                "operator_name": operator_name,
                "bot": bot,
            },
        )
        return {"ok": True, "bot": bot}

    @app.post("/api/setup/telethon/start", dependencies=[Depends(auth)])
    async def telethon_start(payload: Annotated[dict, Body()]) -> dict[str, str]:
        try:
            api_id = int(payload.get("api_id", 0))
            session_path = str(payload.get("session_path", "./secrets/user.session"))
            resolved_session = _safe_path(project_root, session_path)
            result = await manager.start(
                api_id=api_id,
                api_hash=str(payload.get("api_hash", "")),
                phone=str(payload.get("phone", "")),
                passphrase=str(payload.get("passphrase", "")),
                session_path=str(resolved_session),
                env_session_path=session_path,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit_event(
            "telegram_authorisation",
            "telethon_authorisation_started",
            component="webpanel.setup",
            payload={
                "api_id": api_id,
                "phone": str(payload.get("phone", "")),
                "session_path": session_path,
                "result": result,
            },
        )
        return result

    @app.post("/api/setup/telethon/code", dependencies=[Depends(auth)])
    async def telethon_code(payload: Annotated[dict, Body()]) -> dict[str, str]:
        try:
            result = await manager.submit_code(
                str(payload.get("attempt_id", "")), str(payload.get("code", ""))
            )
            if result.get("state") == "ready":
                changed()
            audit_event(
                "telegram_authorisation",
                "telethon_code_submitted",
                component="webpanel.setup",
                payload={
                    "attempt_id": str(payload.get("attempt_id", "")),
                    "result": result,
                },
            )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/setup/telethon/password", dependencies=[Depends(auth)])
    async def telethon_password(payload: Annotated[dict, Body()]) -> dict[str, str]:
        try:
            result = await manager.submit_password(
                str(payload.get("attempt_id", "")), str(payload.get("password", ""))
            )
            if result.get("state") == "ready":
                changed()
            audit_event(
                "telegram_authorisation",
                "telethon_password_submitted",
                component="webpanel.setup",
                payload={
                    "attempt_id": str(payload.get("attempt_id", "")),
                    "result": result,
                },
            )
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/setup/telethon/cancel", dependencies=[Depends(auth)])
    async def telethon_cancel(payload: Annotated[dict, Body()]) -> dict[str, bool]:
        attempt_id = str(payload.get("attempt_id", ""))
        await manager.cancel(attempt_id)
        audit_event(
            "telegram_authorisation",
            "telethon_authorisation_cancelled",
            component="webpanel.setup",
            payload={"attempt_id": attempt_id},
        )
        return {"ok": True}

    @app.post("/api/setup/signing-key", dependencies=[Depends(auth)])
    def signing_key(payload: Annotated[dict, Body()]) -> dict[str, object]:
        requested = str(payload.get("path", "./secrets/signing_key.pem"))
        try:
            path = _safe_path(project_root, requested)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if path.exists():
            audit_event(
                "signing_key",
                "signing_key_reused",
                component="webpanel.setup",
                payload={"path": _display_path(project_root, path)},
            )
            return {"ok": True, "created": False, "path": _display_path(project_root, path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        generate_keypair(str(path), str(path) + ".pub")
        os.chmod(path, 0o600)
        store.save({"HIVE_SIGNING_KEY_PATH": requested})
        changed()
        audit_event(
            "signing_key",
            "signing_key_created",
            component="webpanel.setup",
            payload={"path": _display_path(project_root, path)},
        )
        return {"ok": True, "created": True, "path": _display_path(project_root, path)}

    return manager


def _safe_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("credential paths must stay inside the project directory")
    return resolved


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _positive_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def panel_page(session_token: str) -> str:
    template = PANEL_TEMPLATE_PATH.read_text(encoding="utf-8")
    asset_version = max(PANEL_CSS_PATH.stat().st_mtime_ns, PANEL_JS_PATH.stat().st_mtime_ns)
    return (
        template.replace("__SESSION_TOKEN__", session_token)
        .replace("__LOGO__", LOGO_HTML)
        .replace("__ASSET_VERSION__", str(asset_version))
    )


_SETUP_PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>HIVE Control Panel</title><link rel=icon type=image/png href=/favicon.png>
<style>
:root{color-scheme:dark;--bg:#101216;--panel:#181b21;--line:#30343d;--text:#f1f2f4;--muted:#9ba2ad;--amber:#dfa438;--green:#52ba72;--red:#ef6a6a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif;letter-spacing:0}
header{height:64px;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 28px;gap:12px}.brandmark{width:40px;height:40px;overflow:hidden;display:block;background:transparent;border-radius:4px}.brandmark img{width:100%;height:100%;object-fit:cover;transform:scale(1.55)}h1{font-size:18px;margin:0}header span{color:var(--muted)}
main{display:grid;grid-template-columns:220px minmax(0,760px);gap:40px;max-width:1080px;margin:40px auto;padding:0 24px}
nav{display:flex;flex-direction:column;gap:4px}.nav{border:0;background:transparent;color:var(--muted);text-align:left;padding:10px 12px;border-radius:4px;cursor:pointer}.nav.active{background:#242831;color:var(--text)}
.check{display:inline-block;width:9px;height:9px;border-radius:50%;background:#555;margin-right:9px}.check.ok{background:var(--green)}
section{display:none}section.active{display:block}h2{font-size:22px;margin:0 0 6px}p{color:var(--muted);margin:0 0 24px;line-height:1.5}
.form{border-top:1px solid var(--line);padding-top:20px;display:grid;grid-template-columns:1fr 1fr;gap:16px}.full{grid-column:1/-1}label{display:grid;gap:7px;color:#c9cdd3;font-weight:600}
input{width:100%;background:var(--panel);border:1px solid var(--line);border-radius:4px;color:var(--text);padding:10px 11px;font:inherit;min-width:0}input:focus{outline:2px solid #705929;border-color:var(--amber)}
button.action{border:0;border-radius:4px;background:var(--amber);color:#19150e;padding:10px 15px;font-weight:700;cursor:pointer}button.secondary{background:#2b3039;color:var(--text)}button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;align-items:center;gap:10px}.message{min-height:22px;color:var(--muted)}.message.bad{color:var(--red)}.message.good{color:var(--green)}
.summary{border-top:1px solid var(--line);margin-top:24px;padding-top:18px;display:grid;gap:10px}.summary div{display:flex;justify-content:space-between}.ready{color:var(--green)}
.statusline{border-top:1px solid var(--line);padding:18px 0;display:flex;align-items:center;justify-content:space-between;gap:16px}.state{font-size:18px;font-weight:700}.state.running{color:var(--green)}.state.error{color:var(--red)}.state.restart_required{color:var(--amber)}
.tablewrap{overflow:auto;border-top:1px solid var(--line);margin-top:20px;padding-top:16px}table{border-collapse:collapse;width:100%;min-width:680px}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}th{color:var(--muted)}#detail{white-space:pre-wrap;background:var(--panel);padding:12px;margin-top:16px;max-height:300px;overflow:auto}
@media(max-width:720px){main{grid-template-columns:1fr;margin-top:24px;gap:24px}nav{flex-direction:row;overflow:auto}.nav{white-space:nowrap}.form{grid-template-columns:1fr}.full{grid-column:1}header{padding:0 18px}}
</style></head><body><header>__LOGO__<div><h1>HIVE Control Panel</h1><span>localhost operations and provisioning</span></div></header>
<main><nav>
<button class="nav active" data-tab=operations>Operations</button>
<button class=nav data-tab=sessions>Sessions</button>
<button class=nav data-tab=llm><i class="check setup-check"></i>Models</button>
<button class=nav data-tab=bot><i class="check setup-check"></i>Control bot</button>
<button class=nav data-tab=telethon><i class="check setup-check"></i>Telegram account</button>
<button class=nav data-tab=security><i class="check setup-check"></i>Security</button>
<button class=nav data-tab=review><i class="check setup-check"></i>Review</button>
</nav><div>
<section id=operations class=active><h2>Operations</h2><p>The control panel stays online while the Telegram agent starts or restarts.</p>
<div class=statusline><div><div class=state id=runtimeState>Loading</div><span class=message id=runtimeMeta></span></div><div class=row><button class=action id=startAgent onclick=startAgentNow()>Start agent</button><button class="action secondary" id=restartAgent onclick=restartAgentNow()>Restart</button><button class="action secondary" id=stopAgent onclick=stopAgentNow()>Stop</button></div></div>
<div class=summary id=runtimeChecks></div><span class=message id=runtimeMsg></span></section>
<section id=sessions><h2>Sessions</h2><div class=form>
<label>Peer ID<input id=peerId inputmode=numeric></label><label>Persona<select id=persona style="width:100%;background:var(--panel);border:1px solid var(--line);border-radius:4px;color:var(--text);padding:10px"><option>confused_elderly</option><option>naive_young_adult</option><option>overseas_worker</option><option>small_business_owner</option></select></label>
<div class="full row"><button class=action onclick=startTakeover()>Start takeover</button><button class="action secondary" onclick=refreshSessions()>Refresh</button><span class=message id=sessionMsg></span></div></div>
<div class=tablewrap><h3>Takeover requests</h3><table><thead><tr><th>Peer</th><th>Account</th><th>Last message</th><th>Received</th><th></th></tr></thead><tbody id=chatRows></tbody></table></div>
<div class=tablewrap><h3>Active takeovers</h3><table><thead><tr><th>Peer</th><th>Persona</th><th>Phase</th><th>Verdict</th><th>Score</th><th>Turns</th><th></th></tr></thead><tbody id=sessionRows></tbody></table></div><div id=detail>Select a session to inspect.</div></section>
<section id=llm><h2>Models</h2><p>Configure the OpenAI-compatible endpoint used by the agent.</p><div class=form>
<label class=full>API key<input id=llmKey type=password autocomplete=off></label><label class=full>Hugging Face token (optional)<input id=hfToken type=password autocomplete=off></label><label class=full>Base URL<input id=llmUrl value="https://ollama.com/v1"></label>
<label>Default model<input id=modelCheap value="glm-5.1:cloud"></label><label>Strong model<input id=modelStrong value="glm-5.2:cloud"></label><label>Verdict model<input id=modelLight value="glm-5.1:cloud"></label><label>Vision model<input id=visionModel value="qwen3.5:cloud"></label>
<div class="full row"><button class=action onclick=saveModels()>Save models</button><span class=message id=llmMsg></span></div></div></section>
<section id=bot><h2>Control bot</h2><p>Verify the BotFather token against Telegram and bind commands to one operator ID.</p><div class=form>
<label class=full>Bot API token<input id=botToken type=password autocomplete=off></label><label>Operator Telegram ID<input id=operatorId inputmode=numeric></label>
<div class="full row"><button class=action onclick=verifyBot()>Verify and save</button><span class=message id=botMsg></span></div></div></section>
<section id=telethon><h2>Telegram account</h2><p>Authorize the user account used for scam conversations. The resulting session is encrypted locally.</p><div class=form>
<label>API ID<input id=apiId inputmode=numeric></label><label>API hash<input id=apiHash type=password autocomplete=off></label><label>Phone number<input id=phone placeholder="+60123456789"></label><label>Session passphrase<input id=passphrase type=password autocomplete=new-password></label><label class=full>Encrypted session path<input id=sessionPath value="./secrets/user.session"></label>
<div class="full row"><button class=action id=sendCode onclick=startLogin()>Send code</button><span class=message id=tgMsg></span></div>
<label id=codeBox hidden>Telegram login code<input id=loginCode inputmode=numeric autocomplete=one-time-code></label><div id=codeAction class=row hidden><button class=action onclick=submitCode()>Continue</button></div>
<label id=passwordBox hidden>Telegram 2FA password<input id=tgPassword type=password autocomplete=current-password></label><div id=passwordAction class=row hidden><button class=action onclick=submitPassword()>Authorize</button></div></div></section>
<section id=security><h2>Security</h2><p>Create the evidence signing key and optionally set a persistent API token.</p><div class=form>
<label class=full>Signing key path<input id=signingPath value="./secrets/signing_key.pem"></label><div class="full row"><button class=action onclick=makeKey()>Create signing key</button><span class=message id=keyMsg></span></div>
<label class=full>Persistent API token (optional)<input id=panelToken type=password autocomplete=new-password></label><div class="full row"><button class="action secondary" onclick=savePanel()>Save API token</button><span class=message id=panelMsg></span></div></div></section>
<section id=review><h2>Review</h2><p>HIVE can start when each required component is ready.</p><div class=summary id=summary></div><div class="row" style="margin-top:24px"><button class=action onclick=refresh()>Refresh status</button><span class=message id=readyMsg></span></div></section>
</div></main><script>
const params=new URLSearchParams(location.hash.slice(1)),INJECTED_TOKEN='__SESSION_TOKEN__',TOKEN=params.get('token')||INJECTED_TOKEN||sessionStorage.getItem('hivePanelToken')||'';if(TOKEN)sessionStorage.setItem('hivePanelToken',TOKEN);history.replaceState({},'',location.pathname);let attempt='';
function headers(){return {'X-HIVE-Token':TOKEN,'Content-Type':'application/json'}}
async function call(url,options={}){options.headers=headers();let r=await fetch(url,options),j=await r.json().catch(()=>({}));if(!r.ok){let detail=typeof j.detail==='string'?j.detail:(j.detail?.code||'Request failed');let e=new Error(detail);e.payload=j;e.status=r.status;throw e}return j}
function msg(id,text,ok=true){let e=document.getElementById(id);e.textContent=text;e.className='message '+(ok?'good':'bad')}
function esc(value){return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]))}let sessionsRefreshing=false;
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>{document.querySelectorAll('.nav,section').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.tab).classList.add('active');if(b.dataset.tab==='review')refresh();if(b.dataset.tab==='operations')refreshRuntime();if(b.dataset.tab==='sessions')refreshSessions()});
async function refreshRuntime(){try{let j=await call('/api/runtime/status');runtimeState.textContent=j.state.replace('_',' ');runtimeState.className='state '+j.state;runtimeMeta.textContent=`${j.active_sessions} active session${j.active_sessions===1?'':'s'}`;runtimeChecks.innerHTML=Object.entries(j.checks).map(([k,v])=>`<div><span>${k.replace('_',' ')}</span><strong class=${v?'ready':''}>${v?'Ready':'Missing'}</strong></div>`).join('');startAgent.disabled=j.running||!j.ready;restartAgent.disabled=!j.running;stopAgent.disabled=!j.running;runtimeMsg.textContent=j.error||'';runtimeMsg.className='message '+(j.error?'bad':'')}catch(e){msg('runtimeMsg',e.message,false)}}
async function startAgentNow(){try{await call('/api/runtime/start',{method:'POST'});msg('runtimeMsg','Agent started');refreshRuntime()}catch(e){msg('runtimeMsg',e.message,false);refreshRuntime()}}
async function restartAgentNow(){try{await call('/api/runtime/restart',{method:'POST',body:'{}'});msg('runtimeMsg','Agent restarted');refreshRuntime()}catch(e){if(e.payload?.detail?.code==='active_sessions'&&confirm(`Force restart and discard ${e.payload.detail.count} active takeover(s)?`)){try{await call('/api/runtime/restart',{method:'POST',body:JSON.stringify({force:true})});msg('runtimeMsg','Agent restarted')}catch(forceError){msg('runtimeMsg',forceError.message,false)}}else msg('runtimeMsg',e.message,false);refreshRuntime()}}
async function stopAgentNow(){try{await call('/api/runtime/stop',{method:'POST',body:'{}'});msg('runtimeMsg','Agent stopped');refreshRuntime()}catch(e){msg('runtimeMsg',e.message,false)}}
async function refreshSessions(){if(sessionsRefreshing)return;sessionsRefreshing=true;try{let [chats,rows]=await Promise.all([call('/api/chats'),call('/api/sessions')]);chatRows.innerHTML=chats.map(c=>`<tr><td>${c.peer_id}</td><td>${esc(c.name||('Peer '+c.peer_id))}${c.username?`<br><span class=message>@${esc(c.username)}</span>`:''}</td><td>${esc(c.last_message)||'<span class=message>Media or empty message</span>'}</td><td>${new Date(c.last_message_at*1000).toLocaleString()}</td><td>${c.active?'<span class="ready">Active</span>':`<button class="action secondary" onclick="startObserved(${c.peer_id})">Start</button>`}</td></tr>`).join('');sessionRows.innerHTML=rows.map(s=>`<tr><td>${s.peer_id}</td><td>${esc(s.persona)}</td><td>${esc(s.phase)}</td><td>${esc(s.verdict)}</td><td>${s.score}</td><td>${s.turns}</td><td><button class="action secondary" onclick="showSession(${s.peer_id})">View</button> <button class="action secondary" onclick="endSession(${s.peer_id})">Stop</button></td></tr>`).join('');sessionMsg.textContent=chats.length||rows.length?'':'No takeover requests or active takeovers'}catch(e){chatRows.innerHTML='';sessionRows.innerHTML='';msg('sessionMsg',e.message==='agent_not_running'?'Agent is not running':e.message,false)}finally{sessionsRefreshing=false}}
async function startTakeover(){try{await call('/api/takeover',{method:'POST',body:JSON.stringify({peer_id:parseInt(peerId.value),persona:persona.value})});msg('sessionMsg','Takeover started');refreshSessions();refreshRuntime()}catch(e){msg('sessionMsg',e.message,false)}}
async function startObserved(peer){peerId.value=peer;await startTakeover()}
async function showSession(peer){try{let j=await call('/api/sessions/'+peer);detail.textContent=JSON.stringify(j,null,2)}catch(e){msg('sessionMsg',e.message,false)}}
async function endSession(peer){if(!confirm(`Stop and seal evidence for ${peer}?`))return;try{let j=await call('/api/sessions/'+peer+'/stop',{method:'POST'});detail.textContent=j.summary;window.open('/api/sessions/'+peer+'/evidence?token='+encodeURIComponent(TOKEN),'_blank');refreshSessions();refreshRuntime()}catch(e){msg('sessionMsg',e.message,false)}}
async function saveModels(){try{await call('/api/setup/config',{method:'PUT',body:JSON.stringify({HF_TOKEN:hfToken.value,HIVE_LLM_API_KEY:llmKey.value,HIVE_LLM_BASE_URL:llmUrl.value,HIVE_LLM_MODEL_CHEAP:modelCheap.value,HIVE_LLM_MODEL_STRONG:modelStrong.value,HIVE_LLM_MODEL_LIGHT:modelLight.value,HIVE_VISION_MODEL:visionModel.value})});msg('llmMsg','Saved');refresh()}catch(e){msg('llmMsg',e.message,false)}}
async function verifyBot(){try{let j=await call('/api/setup/bot/verify',{method:'POST',body:JSON.stringify({token:botToken.value,operator_id:operatorId.value})});msg('botMsg','Verified @'+j.bot.username);refresh()}catch(e){msg('botMsg',e.message,false)}}
async function startLogin(){try{let j=await call('/api/setup/telethon/start',{method:'POST',body:JSON.stringify({api_id:apiId.value,api_hash:apiHash.value,phone:phone.value,passphrase:passphrase.value,session_path:sessionPath.value})});attempt=j.attempt_id;codeBox.hidden=false;codeAction.hidden=false;sendCode.disabled=true;msg('tgMsg','Code sent')}catch(e){msg('tgMsg',e.message,false)}}
async function submitCode(){try{let j=await call('/api/setup/telethon/code',{method:'POST',body:JSON.stringify({attempt_id:attempt,code:loginCode.value})});if(j.state==='password_required'){passwordBox.hidden=false;passwordAction.hidden=false;msg('tgMsg','2FA password required')}else loginReady()}catch(e){msg('tgMsg',e.message,false)}}
async function submitPassword(){try{await call('/api/setup/telethon/password',{method:'POST',body:JSON.stringify({attempt_id:attempt,password:tgPassword.value})});loginReady()}catch(e){msg('tgMsg',e.message,false)}}
function loginReady(){msg('tgMsg','Telegram account authorized');codeBox.hidden=true;codeAction.hidden=true;passwordBox.hidden=true;passwordAction.hidden=true;refresh()}
async function makeKey(){try{let j=await call('/api/setup/signing-key',{method:'POST',body:JSON.stringify({path:signingPath.value})});msg('keyMsg',j.created?'Key created':'Key already exists');refresh()}catch(e){msg('keyMsg',e.message,false)}}
async function savePanel(){try{await call('/api/setup/config',{method:'PUT',body:JSON.stringify({HIVE_PANEL_TOKEN:panelToken.value})});msg('panelMsg','Saved');refresh()}catch(e){msg('panelMsg',e.message,false)}}
async function refresh(){try{let j=await call('/api/setup/status');let keys=['llm','control_bot','telethon','signing_key','panel'];document.querySelectorAll('.setup-check').forEach((e,i)=>e.classList.toggle('ok',!!j.checks[keys[i]]));summary.innerHTML=Object.entries(j.checks).map(([k,v])=>`<div><span>${k.replace('_',' ')}</span><strong class=${v?'ready':''}>${v?'Ready':(k==='panel'?'Optional':'Missing')}</strong></div>`).join('');readyMsg.textContent=j.ready?'Ready to start agent':'Setup incomplete';readyMsg.className='message '+(j.ready?'good':'')}catch(e){readyMsg.textContent='Panel session expired';readyMsg.className='message bad'}}refresh();refreshRuntime();setInterval(()=>{if(sessions.classList.contains('active'))refreshSessions()},5000);
</script></body></html>""".replace("__LOGO__", LOGO_HTML)
