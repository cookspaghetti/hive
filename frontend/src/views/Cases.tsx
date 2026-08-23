import { useMemo, useState } from "react";
import { api, post } from "../api";
import { Button, Chip, EmptyState, ErrorState, LoadingState, PageHeading, Score, Surface, formatDate, formatTime, titleCase, verdictTone } from "../components";
import { usePolling } from "../hooks";
import type { CaseSummary, PendingChat } from "../types";

interface CaseData { chats: PendingChat[]; sessions: CaseSummary[]; history: CaseSummary[] }

export function CasesView({ navigate }: { navigate: (route: string) => void }) {
  const [search, setSearch] = useState("");
  const [persona, setPersona] = useState("confused_elderly");
  const [busyPeer, setBusyPeer] = useState<number | null>(null);
  const { data, error, loading, refresh } = usePolling(async () => {
    const results = await Promise.allSettled([
      api<PendingChat[]>("/api/chats"), api<CaseSummary[]>("/api/sessions"), api<CaseSummary[]>("/api/history"),
    ]);
    return {
      chats: results[0].status === "fulfilled" ? results[0].value : [],
      sessions: results[1].status === "fulfilled" ? results[1].value : [],
      history: results[2].status === "fulfilled" ? results[2].value : [],
    };
  }, 5000, []);
  const query = search.trim().toLowerCase();
  const filtered = useMemo(() => ({
    chats: (data?.chats || []).filter((row) => `${row.peer_id} ${row.name} ${row.username} ${row.last_message}`.toLowerCase().includes(query)),
    sessions: (data?.sessions || []).filter((row) => `${row.peer_id} ${row.name} ${row.persona} ${row.verdict}`.toLowerCase().includes(query)),
    history: (data?.history || []).filter((row) => `${row.peer_id} ${row.id} ${row.verdict}`.toLowerCase().includes(query)),
  }), [data, query]);

  async function takeOver(peerId: number) {
    setBusyPeer(peerId);
    try {
      await post("/api/takeover", { peer_id: peerId, persona });
      await refresh();
      navigate(`case/live/${peerId}`);
    } catch (reason) { window.alert(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusyPeer(null); }
  }

  if (loading && !data) return <LoadingState label="Loading cases" />;
  if (error && !data) return <ErrorState message={error} retry={() => void refresh()} />;
  return <div className="view cases-view">
    <PageHeading eyebrow="Operations" title="Cases" copy="Review new conversations, monitor active takeovers, and reopen sealed evidence without changing context." />
    <div className="case-toolbar"><label className="search-control"><span className="sr-only">Search cases</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search peer, account, verdict, or message" /></label><label><span>Persona for new takeover</span><select value={persona} onChange={(event) => setPersona(event.target.value)}><option value="confused_elderly">Confused elderly</option><option value="naive_young_adult">Naive young adult</option><option value="overseas_worker">Overseas worker</option><option value="small_business_owner">Small business owner</option></select></label></div>
    <div className="case-sections">
      <Surface><header className="section-line"><h2>Queue</h2><Chip tone="warning">{filtered.chats.length}</Chip></header>{filtered.chats.length ? <div className="request-list">{filtered.chats.map((chat) => <article key={chat.peer_id}><div><strong>{chat.name || chat.username || `Peer ${chat.peer_id}`}</strong><small>{chat.peer_id} · {formatTime(chat.last_message_at)}</small><p>{chat.last_message || "Media or empty message"}</p></div><div><Button tone="primary" disabled={busyPeer === chat.peer_id} onClick={() => void takeOver(chat.peer_id)}>{busyPeer === chat.peer_id ? "Starting…" : "Take over"}</Button></div></article>)}</div> : <EmptyState title="Queue clear" copy="There are no pending takeover requests matching this view." />}</Surface>
      <Surface><header className="section-line"><h2>Live</h2><Chip tone="success">{filtered.sessions.length}</Chip></header>{filtered.sessions.length ? <div className="case-list">{filtered.sessions.map((item) => <button className="case-row" key={item.peer_id} onClick={() => navigate(`case/live/${item.peer_id}`)}><span className="case-state active"/><div><strong>{item.name || `Peer ${item.peer_id}`}</strong><small>{item.peer_id}</small></div><Chip tone={verdictTone(item.verdict)}>{titleCase(item.verdict)}</Chip><Score value={item.score}/><span>{titleCase(item.persona)}</span><time>{item.turns || 0} turns</time><b>›</b></button>)}</div> : <EmptyState title="No live takeovers" copy="Approve a queued conversation to begin a controlled engagement." />}</Surface>
      <Surface><header className="section-line"><h2>Sealed</h2><Chip>{filtered.history.length}</Chip></header>{filtered.history.length ? <div className="case-list">{filtered.history.map((item) => <button className="case-row" key={item.id || item.history_id} onClick={() => navigate(`case/history/${item.id || item.history_id}`)}><span className="case-state sealed"/><div><strong>{item.name || `Peer ${item.peer_id}`}</strong><small>{item.peer_id}</small></div><Chip tone={verdictTone(item.verdict)}>{titleCase(item.verdict)}</Chip><Score value={item.score}/><span>{item.message_count || item.turns || 0} messages</span><time>{formatDate(item.ended_ts)}</time><b>›</b></button>)}</div> : <EmptyState title="No sealed cases" copy="Completed takeovers will remain available here with their evidence and provenance." />}</Surface>
    </div>
  </div>;
}
