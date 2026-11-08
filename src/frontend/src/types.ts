export type Status = 'pending' | 'queued' | 'running' | 'success' | 'partial_success' | 'failed' | 'blocked' | 'skipped' | 'stopping' | 'stopped' | 'completed' | 'incomplete';
export interface Requirements {
  topic: string | null; budget: string | null; deadline: string | null;
  start_at: string | null; real_sources_required: boolean;
  prefer_web?: boolean;
}
export interface Session {
  session_id: string; title: string; last_user_activity_at: string; status: string;
}
export interface Step {
  step_id: string; key: string; title: string; goal: string; tool: string;
  dependencies: string[]; status: Status; tool_call_ids: string[];
  blocked_reason?: string;
  reused_from_run?: string;
}
export interface Plan {
  plan_version_id: string; version: number; label: string; status: string;
  steps: Step[]; change_summary: string[]; production_spec: 'premium' | 'simple';
  requirements: Requirements;
  estimated_cost?: string;
}
export interface Run {
  run_id: string; status: Status; phase: string; reason: string | null;
  plan_version_id: string; control_status?: string | null; loaded_skills: string[];
}
export interface Costs {
  production: string | null; budget: string | null; tool_simulated: string;
  model_input_tokens: number | null; model_output_tokens: number | null;
  model_unknown_usage_calls?: number;
  model_cost: string | null; estimated_finish: string | null; estimate_stale: boolean;
  model_cost_currency?: string | null;
}
export interface Material {
  material_id: string; title: string; summary: string; source_type: 'synthetic' | 'web';
  url: string | null; retrieved_at: string;
}
export interface ProposalRef { proposal_id: string; version: number; title: string; saved_at: string; plan_version_id: string; }
export interface Proposal extends ProposalRef {
  markdown: string; requirements: Requirements; production_cost: string;
  estimated_finish: string; materials: Material[]; run_id: string;
}
export interface Workspace {
  session: Session; requirements: Requirements; plans: Plan[]; active_run: Run | null;
  cost_summary: Costs; materials: Material[]; proposals: ProposalRef[];
  model_display: string; mode: 'demo' | 'live' | 'offline'; last_event_seq: number;
  pending_changes: string | null;
  requirements_issues?: string[];
}
export interface TimelineItem {
  item_id: string; item_seq: number; kind: 'message' | 'tool'; role?: 'user' | 'assistant';
  content?: string; tool_call_id?: string; created_at: string; processing?: boolean;
}
export interface ToolCall {
  reused_in_step_ids?: string[];
  tool_call_id: string; run_id: string; step_id: string; tool_name: string;
  display_name: string; server_name: string; status: Status; input: unknown; output: unknown;
  started_at: string; duration_ms: number | null; error: string | null; attempt: number;
}
export interface Page<T> { items: T[]; next_cursor: string | null; has_more: boolean; }
export interface ViewContext { plan_version_id?: string; proposal_id?: string; }
export interface StudioEvent { event_id: string; session_seq: number; type: string; session_id: string; payload: Record<string, unknown>; }
export type Connection = 'connecting' | 'connected' | 'reconnecting' | 'offline';
export interface StudioApi {
  mode: 'demo' | 'http';
  listSessions(): Promise<Page<Session>>;
  createSession(): Promise<Session>;
  getWorkspace(id: string): Promise<Workspace>;
  getMessages(id: string, cursor?: string): Promise<Page<TimelineItem>>;
  getToolCall(id: string): Promise<ToolCall>;
  getProposal(id: string): Promise<Proposal>;
  sendMessage(id: string, content: string, clientId: string, context: ViewContext): Promise<{message_id: string}>;
  subscribe(id: string, after: number, onEvent: (event: StudioEvent) => void, onState: (state: Connection) => void): () => void;
}
