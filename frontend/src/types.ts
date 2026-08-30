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
  processing?: boolean;
  analysis_pending?: boolean;
  started_ts?: number;
  ended_ts?: number;
  last_message_ts?: number;
  message_count?: number;
  showcase?: boolean | JsonRecord;
  showcase_label?: string;
  threat_intelligence_items?: number;
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
  media_sha256?: string;
  media_size?: number;
  content_type?: string;
}

export interface Indicator extends JsonRecord {
  kind?: string;
  value?: string;
  confidence?: number;
  source_msg_id?: number;
  extractor?: string;
  review_id?: string;
  review_status?: string;
  reviewed_ts?: number;
}

export interface CaseDetail extends CaseSummary {
  session_id?: string;
  messages?: MessageItem[];
  hvi_items?: Indicator[];
  sandbox_results?: JsonRecord[];
  threat_intelligence?: ThreatIntelObservation[];
  signals?: JsonRecord[];
  signal_trail?: JsonRecord[];
  assessment_history?: JsonRecord[];
  media_analysis?: JsonRecord[];
  scam_vector?: JsonRecord;
  case_intelligence?: JsonRecord;
  reporting_guidance?: JsonRecord;
  selected_analysis?: JsonRecord;
  peer_identity?: JsonRecord;
  related_cases?: RelatedCase[];
  operator_name?: string;
  evidence_download_url?: string;
  package_download_url?: string;
  evidence_verification?: JsonRecord;
}

export interface ThreatIntelObservation extends JsonRecord {
  id?: string;
  provider?: string;
  provider_label?: string;
  indicator_kind?: string;
  observable?: string;
  source_msg_id?: number;
  status?: string;
  risk?: string;
  summary?: string;
  facts?: JsonRecord;
  checked_ts?: number;
  expires_ts?: number;
  cached?: boolean;
  source_url?: string;
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
  metadata_url?: string;
}

export interface CharacterTurnScore {
  turn: number;
  verdict: "pass" | "break" | "uncertain";
  reason: string;
  findings: Array<{category: string; quote: string; reason: string}>;
}

export interface CharacterAssessment {
  rubric_version: string;
  status: string;
  source_sha256: string;
  target_turns: number | null;
  observed_turns: number;
  turns: CharacterTurnScore[];
  error?: string | null;
  judge_model?: string;
}

export interface CharacterMetrics {
  eligible: boolean;
  assessed_turns: number;
  uncertain_turns: number;
  break_turns: number;
  first_break_turn: number | null;
  response_break_rate: number | null;
  session_break: boolean | null;
  break_free_completion: boolean | null;
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
  verdict_correct?: boolean;
  turns?: number;
  exchanges?: number;
  duration_s?: number;
  character_status?: string;
  character_target_turns?: number | null;
  character_metrics?: CharacterMetrics;
  character_assessment?: CharacterAssessment;
  character_automated_assessment?: CharacterAssessment;
  character_response_turns?: Array<{turn:number; messages:string[]; transcript_indices:number[]}>;
  character_rubric?: {categories:Record<string,string>; instructions:string};
  character_persona?: string;
  character_review?: {id:string; reviewer:string; note:string; reviewed_utc:string} | null;
  planned_response_delay_s?: number;
  mean_response_latency_s?: number;
  response_latencies_s?: number[];
  agent_language?: string;
  language_match?: boolean;
  hvi_count?: number;
  f1?: number;
  bot_detected?: boolean;
  bot_probes?: number;
  seed_bot_probes?: number;
  seed_bot_detected?: boolean;
  outbound_guardrail_flags?: number;
  evidence_verified?: boolean;
  package_available?: boolean;
  package_download_url?: string;
  transcript?: Array<[string, string]>;
  hvi_items?: Indicator[];
  threat_intelligence_items?: ThreatIntelObservation[];
}

export interface DemoScenario extends JsonRecord {
  id?: string;
  key?: string;
  name?: string;
  title?: string;
  description?: string;
  language?: string;
  archetype?: string;
  category?: string;
  reference?: string;
  exchanges?: number;
  attachments?: number;
  attachment_types?: string[];
  content_types?: string[];
  live_services?: boolean;
  provider_mode?: string;
  sandbox_mode?: string;
  tags?: string[];
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
  sandbox_results?: JsonRecord[];
  threat_intelligence?: ThreatIntelObservation[];
  signal_trail?: JsonRecord[];
  timeline?: ActivityItem[];
  evidence_download_url?: string;
  live_services?: boolean;
  provider_mode?: string;
  sandbox_mode?: string;
}

export type RouteName = "console" | "cases" | "vault" | "demo" | "evaluations" | "activity" | "logs";

export interface Route {
  name: RouteName | "case";
  source?: "live" | "history";
  id?: string;
}
