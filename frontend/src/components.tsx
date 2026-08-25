import { useEffect, useId, useRef } from "react";
import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";
import type { ActivityItem, Indicator, MessageItem } from "./types";
import { authenticatedUrl } from "./api";
import { CloseIcon, EmptyIcon } from "./icons";

export function Logo() {
  return <img className="hive-logo" src="/logo.png" alt="" />;
}

export function Button({ className = "", tone = "secondary", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "secondary" | "danger" | "quiet" }) {
  return <button className={`button ${tone} ${className}`} type="button" {...props} />;
}

export function IconButton({ label, className = "", children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  const tooltipId = useId();
  return <span className="tooltip"><button className={`icon-button ${className}`} type="button" aria-label={label} aria-describedby={tooltipId} {...props}>{children}</button><span id={tooltipId} role="tooltip">{label}</span></span>;
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
  return <div className="empty-state"><span className="empty-mark"><EmptyIcon size={22}/></span><h3>{title}</h3><p>{copy}</p>{action}</div>;
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return <div className="skeleton-state" role="status" aria-label={label}><span/><span/><span/><span className="sr-only">{label}</span></div>;
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="error-state" role="alert"><div><strong>Could not load this view</strong><p>{message}</p></div>{retry && <Button onClick={retry}>Try again</Button>}</div>;
}

export function Modal({ title, copy, children, actions, onClose, danger = false }: PropsWithChildren<{ title: string; copy?: string; actions?: ReactNode; onClose: () => void; danger?: boolean }>) {
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const descriptionId = useId();
  onCloseRef.current = onClose;
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || []).filter(element => !element.hidden && element.getAttribute("aria-hidden") !== "true");
    const initial = dialog?.querySelector<HTMLElement>('[data-initial-focus], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), footer button:not([disabled]):not(.danger)') || focusable()[0] || dialog;
    window.requestAnimationFrame(() => initial?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onCloseRef.current(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) { event.preventDefault(); dialog?.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => { document.removeEventListener("keydown", handleKeyDown); previousFocus?.focus(); };
  }, []);
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={dialogRef} className={`modal ${danger ? "danger" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={copy ? descriptionId : undefined} tabIndex={-1}><header><div><p className="eyebrow">{danger ? "Irreversible action" : "HIVE"}</p><h2 id={titleId}>{title}</h2>{copy && <p id={descriptionId}>{copy}</p>}</div><IconButton label="Close dialog" onClick={onClose}><CloseIcon/></IconButton></header><div className="modal-body">{children}</div>{actions && <footer>{actions}</footer>}</section></div>;
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

export function RiskBar({ value, compact = false }: { value?: number; compact?: boolean }) {
  const valid = typeof value === "number";
  const score = valid ? Math.max(0, Math.min(1, value)) : 0;
  const level = score >= .75 ? "high" : score >= .45 ? "medium" : "low";
  return <span className={`risk-bar ${compact ? "compact" : ""} ${level}`}><i><b style={{width: valid ? `${Math.max(2, score * 100)}%` : "0%"}}/></i><strong>{valid ? score.toFixed(2) : "—"}</strong></span>;
}

export function Segmented<T extends string>({ value, items, onChange, label }: { value: T; items: Array<{value:T;label:string;count?:number}>; onChange:(value:T)=>void; label:string }) {
  return <div className="segmented" role="group" aria-label={label}>{items.map(item => <button key={item.value} className={value === item.value ? "active" : ""} aria-pressed={value === item.value} onClick={() => onChange(item.value)}>{item.label}{typeof item.count === "number" && <span>{item.count}</span>}</button>)}</div>;
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
  const transcriptRef = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript && followLatest.current) {
      transcript.scrollTo({ top: transcript.scrollHeight, behavior: "smooth" });
    }
  }, [messages.length, thinking]);

  if (!messages.length) return <EmptyState title="No messages yet" copy="The transcript will update when the first exchange is recorded." />;
  return <div className="transcript" ref={transcriptRef} role="log" aria-label="Conversation transcript" aria-live="polite" tabIndex={0} onScroll={(event) => {
    const transcript = event.currentTarget;
    followLatest.current = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
  }}>{messages.map((message, index) => {
    const agent = ["agent", "assistant", "hive", "victim"].includes((message.role || "").toLowerCase());
    return <article className={`message ${agent ? "agent" : "stranger"}`} key={`${message.msg_id ?? index}-${message.timestamp || message.ts}`}><p>{message.text || (message.media_kind ? `${titleCase(message.media_kind)} attachment` : "Empty message")}</p>{message.media_url && message.media_mime?.startsWith("image/") && <img src={authenticatedUrl(message.media_url)} alt={message.media_name || "Captured message attachment"} />}<footer>{agent ? "HIVE" : "Stranger"}<span>·</span>{formatTime(message.timestamp || message.ts)}{message.media_kind && <><span>·</span>{titleCase(message.media_kind)}</>}</footer></article>;
  })}{thinking && <div className="thinking">HIVE is thinking <span>•••</span></div>}</div>;
}
