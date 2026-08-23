export type JsonRecord = Record<string, unknown>;

export interface RuntimeComponent {
  state?: string;
  detail?: string;
  duration_s?: number | null;
}

export interface RuntimeStatus {
  state?: string;
  running?: boolean;
  ready?: boolean;
  restart_required?: boolean;
  active_sessions?: number;
  recovery_paused?: number;
  error?: string;
  checks?: Record<string, boolean>;
  components?: Record<string, RuntimeComponent>;
}

export interface Dashboard {
  runtime: RuntimeStatus;
  metrics: {
    active_sessions: number;
    observed_chats: number;
    likely_scams: number;
    hvis: number;
    sandbox_runs: number;
    turns: number;
  };
  sessions: CaseSummary[];
  chats: PendingChat[];
  activity: ActivityItem[];
}

export interface CaseSummary extends JsonRecord {
  peer_id?: number;
  id?: string;
  history_id?: string;
  name?: string;
  persona?: string;
  phase?: string;
  verdict?: string;
  score?: number;
  turns?: number;
  hvis?: number;
  sandbox?: number;
  recovery_status?: string;
  started_ts?: number;
  ended_ts?: number;
  last_message_ts?: number;
  message_count?: number;
}

export interface PendingChat extends JsonRecord {
  peer_id: number;
  name?: string;
  username?: string;
  last_message?: string;
  last_message_at?: number;
}

export interface ActivityItem extends JsonRecord {
  id?: number;
  time?: string;
  timestamp?: number;
  ts?: number;
  category?: string;
  title?: string;
  detail?: string;
  severity?: string;
}

export interface MessageItem extends JsonRecord {
  role?: string;
  text?: string;
  timestamp?: number;
  ts?: number;
  msg_id?: number;
  media_kind?: string;
  media_name?: string;
  media_url?: string;
  media_mime?: string;
}

export interface Indicator extends JsonRecord {
  kind?: string;
  value?: string;
  confidence?: number;
  source_msg_id?: number;
  extractor?: string;
}

export interface CaseDetail extends CaseSummary {
  session_id?: string;
  messages?: MessageItem[];
  hvi_items?: Indicator[];
  signals?: JsonRecord[];
  assessment_history?: JsonRecord[];
  sandbox_results?: JsonRecord[];
  media_analysis?: JsonRecord[];
  scam_vector?: JsonRecord;
  case_intelligence?: JsonRecord;
  reporting_guidance?: JsonRecord;
  selected_analysis?: JsonRecord;
}

export interface RelatedCase extends JsonRecord {
  related_history_id?: string;
  peer_id?: number;
  relationship?: string;
  score?: number;
  reasons?: JsonRecord[];
  pattern_profile?: JsonRecord;
}

export interface EvidenceRow extends JsonRecord {
  peer_id?: number;
  bundle_id?: string;
  filename?: string;
  created_ts?: number;
  size?: number;
  sha256?: string;
  signature_present?: boolean;
  download_url?: string;
  package_present?: boolean;
  package_filename?: string;
  package_download_url?: string;
}

export interface EvaluationRow extends JsonRecord {
  id: string;
  run_group?: string;
  recorded_ts?: number;
  scenario?: string;
  persona?: string;
  language?: string;
  verdict?: string;
  verdict_score?: number;
  turns?: number;
  f1?: number;
  evidence_verified?: boolean;
  package_available?: boolean;
  package_download_url?: string;
  transcript?: Array<[string, string]>;
  hvi_items?: Indicator[];
}

export interface DemoScenario extends JsonRecord {
  id?: string;
  key?: string;
  name?: string;
  title?: string;
  description?: string;
  language?: string;
  archetype?: string;
  exchanges?: number;
}

export interface DemoRun extends JsonRecord {
  id?: string;
  run_id?: string;
  mode?: string;
  scenario?: string | JsonRecord;
  persona?: string;
  speed?: string;
  status?: string;
  stage?: string;
  verdict?: string;
  score?: number;
  verdict_score?: number;
  exchange?: number;
  current_exchange?: number;
  total_exchanges?: number;
  messages?: MessageItem[];
  hvi_items?: Indicator[];
  timeline?: ActivityItem[];
  evidence_download_url?: string;
}

export type RouteName = "console" | "cases" | "vault" | "demo" | "evaluations" | "activity" | "logs";

export interface Route {
  name: RouteName | "case";
  source?: "live" | "history";
  id?: string;
}
