import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";
import type { ActivityItem, Indicator, MessageItem } from "./types";
import { authenticatedUrl } from "./api";

export function Logo() {
  return <img className="hive-logo" src="/logo.png" alt="" />;
}

export function Button({ className = "", tone = "secondary", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "secondary" | "danger" | "quiet" }) {
  return <button className={`button ${tone} ${className}`} type="button" {...props} />;
}

export function Chip({ children, tone = "neutral" }: PropsWithChildren<{ tone?: string }>) {
  return <span className={`chip ${tone}`}>{children}</span>;
}

export function Surface({ children, className = "" }: PropsWithChildren<{ className?: string }>) {
  return <section className={`surface ${className}`}>{children}</section>;
}

export function SurfaceHeader({ title, copy, action }: { title: string; copy?: string; action?: ReactNode }) {
  return <header className="surface-header"><div><h2>{title}</h2>{copy && <p>{copy}</p>}</div>{action}</header>;
}

export function PageHeading({ eyebrow, title, copy, actions }: { eyebrow?: string; title: string; copy?: string; actions?: ReactNode }) {
  return <header className="page-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{copy && <p>{copy}</p>}</div>{actions && <div className="heading-actions">{actions}</div>}</header>;
}

export function EmptyState({ title, copy, action }: { title: string; copy: string; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-mark" aria-hidden="true">◇</span><h3>{title}</h3><p>{copy}</p>{action}</div>;
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return <div className="loading-state"><span aria-hidden="true" /><span>{label}</span></div>;
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="error-state" role="alert"><div><strong>Could not load this view</strong><p>{message}</p></div>{retry && <Button onClick={retry}>Try again</Button>}</div>;
}

export function Modal({ title, copy, children, actions, onClose, danger = false }: PropsWithChildren<{ title: string; copy?: string; actions?: ReactNode; onClose: () => void; danger?: boolean }>) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className={`modal ${danger ? "danger" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title"><header><div><p className="eyebrow">{danger ? "Irreversible action" : "HIVE"}</p><h2 id="modal-title">{title}</h2>{copy && <p>{copy}</p>}</div><button className="icon-button" type="button" aria-label="Close dialog" onClick={onClose}>×</button></header><div className="modal-body">{children}</div>{actions && <footer>{actions}</footer>}</section></div>;
}

export function formatDate(timestamp?: number, includeTime = false): string {
  if (!timestamp) return "—";
  const value = timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp;
  return new Intl.DateTimeFormat(undefined, includeTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" }).format(new Date(value));
}

export function formatTime(timestamp?: number): string {
  if (!timestamp) return "—";
  const value = timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp;
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function formatBytes(bytes?: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

export function titleCase(value?: unknown): string {
  const candidate = value && typeof value === "object"
    ? (value as Record<string, unknown>).title || (value as Record<string, unknown>).name || (value as Record<string, unknown>).key
    : value;
  return String(candidate || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function verdictTone(verdict?: string): string {
  if (verdict === "likely_scam") return "danger";
  if (verdict === "benign") return "success";
  if (verdict === "uncertain") return "info";
  return "neutral";
}

export function Score({ value }: { value?: number }) {
  const score = typeof value === "number" ? value : 0;
  return <span className={`score ${score >= .75 ? "high" : score >= .45 ? "medium" : "low"}`}>{typeof value === "number" ? score.toFixed(2) : "—"}</span>;
}

export function IndicatorList({ items = [], compact = false }: { items?: Indicator[]; compact?: boolean }) {
  if (!items.length) return <EmptyState title="No indicators yet" copy="Validated values will appear as the conversation develops." />;
  return <div className={`indicator-grid ${compact ? "compact" : ""}`}>{items.map((item, index) => <article className="indicator-card" key={`${item.kind}-${item.value}-${index}`}><div><span>{titleCase(item.kind)}</span>{typeof item.confidence === "number" && <strong>{item.confidence.toFixed(2)}</strong>}</div><code>{item.value || "—"}</code><p>Message {item.source_msg_id ?? "—"} · {item.extractor || "pipeline"}</p></article>)}</div>;
}

export function Timeline({ items = [], emptyCopy = "No activity recorded." }: { items?: ActivityItem[]; emptyCopy?: string }) {
  if (!items.length) return <EmptyState title="Nothing recorded" copy={emptyCopy} />;
  return <div className="timeline">{items.map((item, index) => <article key={`${item.id ?? index}-${item.title}`}><time>{item.time || formatTime(item.timestamp || item.ts)}</time><span className={`timeline-dot ${item.severity || "info"}`} /><div><strong>{item.title || item.category || "Event"}</strong>{item.detail && <p>{item.detail}</p>}</div></article>)}</div>;
}

export function Transcript({ messages = [], thinking = false }: { messages?: MessageItem[]; thinking?: boolean }) {
  if (!messages.length) return <EmptyState title="No messages yet" copy="The transcript will update when the first exchange is recorded." />;
  return <div className="transcript">{messages.map((message, index) => {
    const agent = ["agent", "assistant", "hive", "victim"].includes((message.role || "").toLowerCase());
    return <article className={`message ${agent ? "agent" : "stranger"}`} key={`${message.msg_id ?? index}-${message.timestamp || message.ts}`}><p>{message.text || (message.media_kind ? `${titleCase(message.media_kind)} attachment` : "Empty message")}</p>{message.media_url && message.media_mime?.startsWith("image/") && <img src={authenticatedUrl(message.media_url)} alt={message.media_name || "Captured message attachment"} />}<footer>{agent ? "HIVE" : "Stranger"}<span>·</span>{formatTime(message.timestamp || message.ts)}{message.media_kind && <><span>·</span>{titleCase(message.media_kind)}</>}</footer></article>;
  })}{thinking && <div className="thinking">HIVE is thinking <span>•••</span></div>}</div>;
}
