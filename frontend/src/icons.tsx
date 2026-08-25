import type { SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 18, children, ...props }: IconProps) {
  return <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}>{children}</svg>;
}

export function ConsoleIcon(props: IconProps) { return <Icon {...props}><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="5" rx="2"/><rect x="13" y="10" width="8" height="11" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/></Icon>; }
export function CasesIcon(props: IconProps) { return <Icon {...props}><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r=".8" fill="currentColor" stroke="none"/><circle cx="3.5" cy="12" r=".8" fill="currentColor" stroke="none"/><circle cx="3.5" cy="18" r=".8" fill="currentColor" stroke="none"/></Icon>; }
export function VaultIcon(props: IconProps) { return <Icon {...props}><path d="M4 8h16v12H4zM3 4h18v4H3zM9 12h6"/></Icon>; }
export function ActivityIcon(props: IconProps) { return <Icon {...props}><path d="M3 12h4l2.5-6 4 12 2.5-6h5"/></Icon>; }
export function DemoIcon(props: IconProps) { return <Icon {...props}><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 9 5 3-5 3z"/></Icon>; }
export function EvaluationIcon(props: IconProps) { return <Icon {...props}><path d="M9 5h11M9 12h11M9 19h11M3.5 5l1 1 2-2M3.5 12l1 1 2-2M3.5 19l1 1 2-2"/></Icon>; }
export function LogsIcon(props: IconProps) { return <Icon {...props}><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3M13 15h4"/></Icon>; }
export function SettingsIcon(props: IconProps) { return <Icon {...props}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></Icon>; }
export function MenuIcon(props: IconProps) { return <Icon {...props}><path d="M4 7h16M4 12h16M4 17h16"/></Icon>; }
export function MoreIcon(props: IconProps) { return <Icon {...props}><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/></Icon>; }
export function CloseIcon(props: IconProps) { return <Icon {...props}><path d="m6 6 12 12M18 6 6 18"/></Icon>; }
export function SearchIcon(props: IconProps) { return <Icon {...props}><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/></Icon>; }
export function ChevronLeftIcon(props: IconProps) { return <Icon {...props}><path d="m15 18-6-6 6-6"/></Icon>; }
export function ChevronRightIcon(props: IconProps) { return <Icon {...props}><path d="m9 18 6-6-6-6"/></Icon>; }
export function ChevronDownIcon(props: IconProps) { return <Icon {...props}><path d="m6 9 6 6 6-6"/></Icon>; }
export function ArrowRightIcon(props: IconProps) { return <Icon {...props}><path d="M5 12h14m-6-6 6 6-6 6"/></Icon>; }
export function EmptyIcon(props: IconProps) { return <Icon {...props}><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9"/></Icon>; }
