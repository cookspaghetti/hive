import { api, post } from "../api";
import { Button, Chip, EmptyState, ErrorState, LoadingState, PageHeading, Score, Surface, SurfaceHeader, Timeline, titleCase, verdictTone } from "../components";
import { useNow, usePolling } from "../hooks";
import type { CaseSummary, Dashboard } from "../types";

export function ConsoleView({ navigate, openSetup }: { navigate: (route: string) => void; openSetup: () => void }) {
  const now = useNow();
  const { data, error, loading, refresh } = usePolling(() => api<Dashboard>("/api/dashboard"), 5000, []);
  const runtime = data?.runtime;
  const metrics = data?.metrics;
  const attention = data?.sessions?.find((item) => Number(item.score || 0) >= .85);

  async function runtimeAction(action: "start" | "restart" | "stop") {
    if (action === "stop" && !window.confirm("Stop the HIVE runtime? Telegram delivery and live analysis will pause, while the local panel and stored evidence remain available.")) return;
    try {
      await post(`/api/runtime/${action}`);
      await refresh();
    } catch (reason) {
      window.alert(reason instanceof Error ? reason.message : String(reason));
    }
  }

  if (loading && !data) return <LoadingState label="Loading operations console" />;
  if (error && !data) return <ErrorState message={error} retry={() => void refresh()} />;

  return <div className="view console-view">
    <PageHeading
      eyebrow={new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(now)}
      title={runtime?.running && !metrics?.active_sessions && !metrics?.observed_chats ? "Nothing needs you" : runtime?.running ? `${metrics?.active_sessions || 0} takeovers running, ${metrics?.observed_chats || 0} waiting` : "HIVE is ready when you are"}
      copy={runtime?.running && !metrics?.active_sessions && !metrics?.observed_chats ? "Watching for the first eligible private chat. Requests will appear here and in Telegram." : runtime?.running ? "The console keeps active cases, requests, and operational milestones in one place." : "Review setup, then start the agent when the approved test account is ready."}
      actions={<>{runtime?.running ? <><Button onClick={() => void runtimeAction("restart")}>Restart runtime</Button><Button tone="danger" onClick={() => void runtimeAction("stop")}>Stop</Button></> : <><Button tone="primary" onClick={() => void runtimeAction("start")}>Start agent</Button><Button onClick={openSetup}>Review setup</Button></>}</>}
    />

    {!runtime?.ready && <div className="attention-banner warning"><span>!</span><div><strong>Setup needs attention</strong><p>{runtime?.error || "One or more required components are not ready."}</p></div><Button onClick={openSetup}>Open setup</Button></div>}
    {attention && <div className="attention-banner"><span>•</span><div><strong>Peer {attention.peer_id} crossed {(Number(attention.score) * 100).toFixed(0)}%</strong><p>{attention.hvis || 0} indicators · {attention.sandbox || 0} sandbox runs · review before sealing</p></div><Button onClick={() => navigate(`case/live/${attention.peer_id}`)}>Open case</Button></div>}

    <div className="metric-row">
      {[
        ["Active", metrics?.active_sessions], ["Likely scams", metrics?.likely_scams], ["Indicators", metrics?.hvis], ["Sandbox runs", metrics?.sandbox_runs], ["Agent turns", metrics?.turns],
      ].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{value ?? "—"}</strong></div>)}
    </div>

    <div className="console-grid">
      <Surface className="case-overview">
        <SurfaceHeader title="Cases" copy={data?.sessions.length ? "Highest-risk active cases appear first." : "No active cases."} action={<button className="text-button" onClick={() => navigate("cases")}>Full list</button>} />
        {data?.sessions.length ? <div className="case-list">{data.sessions.map((item) => <CaseRow key={item.peer_id} item={item} onOpen={() => navigate(`case/live/${item.peer_id}`)} />)}</div> : <EmptyState title="Nothing active" copy="Approved takeovers will appear here without replacing the queue." />}
      </Surface>

      <div className="console-side">
        <Surface>
          <SurfaceHeader title="Queue" copy={`${data?.chats.length || 0} waiting`} action={<button className="text-button" onClick={() => navigate("cases")}>Review all</button>} />
          {data?.chats.length ? <div className="queue-list">{data.chats.map((chat) => <article key={chat.peer_id}><div><strong>{chat.name || chat.username || `Peer ${chat.peer_id}`}</strong><p>{chat.last_message || "Media or empty message"}</p></div><Button onClick={() => navigate("cases")}>Review</Button></article>)}</div> : <EmptyState title="Queue clear" copy="Pending private chats will appear here." />}
        </Surface>
        <Surface>
          <SurfaceHeader title="Activity" action={<button className="text-button" onClick={() => navigate("activity")}>Ledger</button>} />
          <Timeline items={data?.activity.slice(0, 5)} />
        </Surface>
      </div>
    </div>
  </div>;
}

function CaseRow({ item, onOpen }: { item: CaseSummary; onOpen: () => void }) {
  return <button className="case-row" onClick={onOpen}><span className="case-state active" /><div><strong>{item.name || `Peer ${item.peer_id}`}</strong><small>{item.peer_id}</small></div><Chip tone={verdictTone(item.verdict)}>{titleCase(item.verdict || "not assessed")}</Chip><Score value={item.score} /><span>{titleCase(item.persona)}</span><time>{item.turns || 0} turns</time><b>›</b></button>;
}
