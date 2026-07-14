"""FastAPI control panel app (fyp.txt control plane, web surface).

create_app(engine, userbot, settings) returns a FastAPI instance that reads the
userbot's live per-peer session registry and drives the engine. All /api routes
require the operator token in the `X-HIVE-Token` header; the HTML shell at `/`
is static and prompts for the token client-side. Bind to localhost only.
"""

from __future__ import annotations

import os

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from hive.agent.personas import PERSONAS
from hive.logging_setup import get_logger

log = get_logger(__name__)

VALID_PERSONAS = set(PERSONAS)


def _session_summary(peer_id, session) -> dict:
    return {
        "peer_id": peer_id,
        "persona": session.persona,
        "phase": session.phase.value if hasattr(session.phase, "value") else str(session.phase),
        "verdict": session.verdict,
        "score": round(session.verdict_score, 3),
        "turns": session.turn_count,
        "hvis": len(session.hvis),
        "sandbox": len(session.sandbox_results),
    }


def _session_detail(peer_id, session) -> dict:
    d = _session_summary(peer_id, session)
    d["messages"] = [{"role": m.role, "text": m.text, "ts": m.ts} for m in session.messages]
    d["hvi_items"] = [
        {"kind": h.kind, "value": h.value, "confidence": round(h.confidence, 2)} for h in session.hvis
    ]
    d["sandbox_results"] = session.sandbox_results
    d["signal_trail"] = session.signal_trail[-20:]
    return d


def create_app(engine, userbot, settings) -> FastAPI:
    app = FastAPI(title="HIVE Control Panel", docs_url=None, redoc_url=None)

    def auth(x_hive_token: str = Header(default="")) -> None:
        if not settings.panel_token or x_hive_token != settings.panel_token:
            raise HTTPException(status_code=401, detail="unauthorised")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.get("/favicon.svg")
    def favicon():
        from fastapi.responses import Response

        return Response(content=_LOGO_SVG, media_type="image/svg+xml")

    @app.get("/api/sessions", dependencies=[Depends(auth)])
    def list_sessions() -> list[dict]:
        return [_session_summary(pid, s) for pid, (s, _c) in userbot._sessions.items()]

    @app.get("/api/sessions/{peer_id}", dependencies=[Depends(auth)])
    def get_session(peer_id: int) -> dict:
        entry = userbot._sessions.get(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        return _session_detail(peer_id, entry[0])

    @app.post("/api/takeover", dependencies=[Depends(auth)])
    def takeover(payload: dict = Body(...)) -> dict:
        peer_id = payload.get("peer_id")
        persona = payload.get("persona") or settings.default_persona
        if not isinstance(peer_id, int):
            raise HTTPException(status_code=400, detail="peer_id must be an integer")
        if persona not in VALID_PERSONAS:
            raise HTTPException(status_code=400, detail=f"unknown persona: {persona}")
        userbot.begin_takeover(peer_id, persona)
        return {"ok": True, "peer_id": peer_id, "persona": persona}

    @app.post("/api/sessions/{peer_id}/persona", dependencies=[Depends(auth)])
    def set_persona(peer_id: int, payload: dict = Body(...)) -> dict:
        entry = userbot._sessions.get(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        persona = payload.get("persona")
        if persona not in VALID_PERSONAS:
            raise HTTPException(status_code=400, detail=f"unknown persona: {persona}")
        entry[0].persona = persona
        return {"ok": True, "peer_id": peer_id, "persona": persona}

    @app.post("/api/sessions/{peer_id}/stop", dependencies=[Depends(auth)])
    def stop(peer_id: int) -> dict:
        entry = userbot.end_takeover(peer_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="no active takeover")
        session, chain = entry
        os.makedirs("evidence", exist_ok=True)
        out_path = f"evidence/bundle_{peer_id}.pdf"
        engine.close_session(session, chain, out_path, settings.signing_key_path)
        log.info("panel: stopped + sealed peer=%s", peer_id)
        return {"ok": True, "summary": engine.summary(session), "bundle": out_path}

    @app.get("/api/sessions/{peer_id}/evidence")
    def evidence(peer_id: int, token: str = "", x_hive_token: str = Header(default="")):
        # File download opens in a new tab (no custom headers possible), so this
        # endpoint accepts the token via query param as well as header.
        supplied = x_hive_token or token
        if not settings.panel_token or supplied != settings.panel_token:
            raise HTTPException(status_code=401, detail="unauthorised")
        path = f"evidence/bundle_{peer_id}.pdf"
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="no sealed bundle")
        return FileResponse(path, media_type="application/pdf", filename=f"evidence_{peer_id}.pdf")

    return app


_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<polygon points="50,4 89.8,27 89.8,73 50,96 10.2,73 10.2,27" fill="#22262e"/>
<polygon points="50,13 82,31.5 82,68.5 50,87 18,68.5 18,31.5" fill="none" stroke="#d99a2b" stroke-width="4" stroke-linejoin="round"/>
<polygon points="50,23 73.4,36.5 73.4,63.5 50,77 26.6,63.5 26.6,36.5" fill="none" stroke="#d99a2b" stroke-width="4" stroke-linejoin="round"/>
<circle cx="50" cy="45" r="6" fill="#d99a2b"/>
<polygon points="46,48 54,48 56,61 44,61" fill="#d99a2b"/>
</svg>"""

_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>HIVE Control Panel</title>
<link rel=icon type=image/svg+xml href=/favicon.svg>
<style>
 body{font-family:system-ui,sans-serif;margin:1.5rem;background:#0f1115;color:#e6e6e6}
 h1{font-size:1.2rem;display:flex;align-items:center;gap:.5rem} h1 svg{width:32px;height:32px}
 input,button,select{padding:.35rem;margin:.15rem;background:#1b1f27;color:#e6e6e6;border:1px solid #333;border-radius:4px}
 table{border-collapse:collapse;width:100%;margin-top:.5rem} th,td{border:1px solid #2a2f3a;padding:.35rem .5rem;text-align:left;font-size:.9rem}
 .scam{color:#ff6b6b} .benign{color:#6bcB77} .inconclusive{color:#e0c060}
 #detail{white-space:pre-wrap;background:#151922;padding:.75rem;border-radius:6px;margin-top:.5rem;max-height:40vh;overflow:auto}
 .bar{margin:.5rem 0}
</style></head><body>
<h1>__LOGO__ HIVE Control Panel <small>(localhost)</small></h1>
<div class=bar>Operator token: <input id=tok type=password size=32> <button onclick=save()>Set</button> <span id=st></span></div>
<div class=bar>
 Peer ID: <input id=peer size=14>
 <select id=persona><option>confused_elderly</option><option>naive_young_adult</option><option>overseas_worker</option><option>small_business_owner</option></select>
 <button onclick=takeover()>Start takeover</button>
</div>
<table id=tbl><thead><tr><th>Peer</th><th>Persona</th><th>Phase</th><th>Verdict</th><th>Score</th><th>Turns</th><th>HVIs</th><th>Sbx</th><th></th></tr></thead><tbody></tbody></table>
<div id=detail>Select a session to inspect.</div>
<script>
let TOK=""; function save(){TOK=document.getElementById('tok').value;document.getElementById('st').textContent='token set';refresh()}
function h(){return {'X-HIVE-Token':TOK,'Content-Type':'application/json'}}
async function refresh(){
 try{let r=await fetch('/api/sessions',{headers:h()}); if(!r.ok){document.getElementById('st').textContent='auth?';return}
 let rows=await r.json(); let tb=document.querySelector('#tbl tbody'); tb.innerHTML='';
 for(const s of rows){let tr=document.createElement('tr');
  tr.innerHTML=`<td>${s.peer_id}</td><td>${s.persona}</td><td>${s.phase}</td><td class=${s.verdict.replace('likely_','')}>${s.verdict}</td><td>${s.score}</td><td>${s.turns}</td><td>${s.hvis}</td><td>${s.sandbox}</td>`+
   `<td><button onclick='detail(${s.peer_id})'>view</button><button onclick='stopS(${s.peer_id})'>stop</button></td>`;
  tb.appendChild(tr);} }catch(e){}
}
async function detail(p){let r=await fetch('/api/sessions/'+p,{headers:h()});document.getElementById('detail').textContent=JSON.stringify(await r.json(),null,2)}
async function takeover(){await fetch('/api/takeover',{method:'POST',headers:h(),body:JSON.stringify({peer_id:parseInt(document.getElementById('peer').value),persona:document.getElementById('persona').value})});refresh()}
async function stopS(p){if(!confirm('Stop + seal evidence for '+p+'?'))return;let r=await fetch('/api/sessions/'+p+'/stop',{method:'POST',headers:h()});let j=await r.json();alert(j.summary||JSON.stringify(j));window.open('/api/sessions/'+p+'/evidence?token='+encodeURIComponent(TOK),'_blank');refresh()}
setInterval(refresh,3000);
</script></body></html>""".replace("__LOGO__", _LOGO_SVG)
