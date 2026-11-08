import type { StudioApi, Workspace, Session, TimelineItem, ToolCall, Proposal, Plan, StudioEvent, Status } from '../types';

type DemoRecord = { workspace: Workspace; messages: TimelineItem[]; calls: Record<string, ToolCall>; proposals: Record<string, Proposal>; clientIds: Record<string, string>; failOnce: boolean; failed: boolean; };
const storageKey = 'content-studio-demo-v1';
let database: Record<string, DemoRecord> = JSON.parse(localStorage.getItem(storageKey) || '{}');
const listeners = new Map<string, Set<(event: StudioEvent) => void>>();
const uid = () => crypto.randomUUID();
const now = () => new Date().toISOString();
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const copy = <T,>(value: T): T => structuredClone(value);
function record(id: string) { const value = database[id]; if (!value) throw new Error('演示会话不存在。'); return value; }
function publish(id: string) {
  const data = record(id); data.workspace.last_event_seq += 1;
  localStorage.setItem(storageKey, JSON.stringify(database));
  const event: StudioEvent = { event_id: `${id}:${data.workspace.last_event_seq}`, session_seq: data.workspace.last_event_seq, session_id: id, type: 'workspace.updated', payload: {} };
  listeners.get(id)?.forEach(listener => listener(event));
}
function message(id: string, content: string, role: 'user' | 'assistant' = 'assistant') {
  const data = record(id); const item_id = uid();
  data.messages.push({ item_id, item_seq: data.messages.length + 1, kind: 'message', role, content, created_at: now() });
  publish(id); return item_id;
}
function parseBrief(id: string, text: string) {
  const data = record(id); const brief = data.workspace.requirements;
  const budget = text.match(/预算(?:改为|改成|调整为|是|为|[:：])?\s*(\d+(?:\.\d+)?)/);
  if (budget) brief.budget = budget[1];
  const date = text.match(/(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:\s+|T)?(\d{1,2})(?::|点|时)(\d{2})?/);
  if (date) brief.deadline = `${date[1]}-${date[2].padStart(2, '0')}-${date[3].padStart(2, '0')}T${date[4].padStart(2, '0')}:${(date[5] || '00').padStart(2, '0')}:00+08:00`;
  if (!brief.topic || /主题(?:改为|是|为)/.test(text)) {
    const topic = text.split(/预算|截止|20\d{2}/)[0].replace(/[，。；,;\s]+$/, '');
    if (topic.length > 2 && !/^(开始|继续|停止|暂停)/.test(topic)) brief.topic = topic;
  }
  if (/必须.*真实素材|仅用真实素材/.test(text)) brief.real_sources_required = true;
  if (/模拟.*失败|演示.*失败/.test(text)) data.failOnce = true;
  data.workspace.cost_summary.budget = brief.budget;
  if (brief.topic) data.workspace.session.title = brief.topic.slice(0, 22);
}
function makePlan(id: string, simple = false, change: string[] = []) {
  const data = record(id); const previous = data.workspace.plans.at(-1);
  const definitions = [
    ['materials', '检索活动素材', '从素材中提炼主题与内容切入点', 'search_synthetic_materials', []],
    ['script', '生成内容脚本', '整理传播主张与可直接使用的文案', 'generate_script', ['materials']],
    ['estimate', '核算制作成本与排期', '校验制作预算及活动完成期限', 'estimate_proposal', ['script']],
    ['validate', '检查方案完整性', '核对章节、交付物及素材引用', 'validate_proposal', ['estimate']],
    ['save', '保存活动方案', '保留方案正文、版本与执行结果', 'save_proposal', ['validate']],
  ] as const;
  const plan: Plan = { plan_version_id: uid(), version: (previous?.version || 0) + 1, label: simple ? '精简制作方案' : '完整内容方案', status: 'ready', production_spec: simple ? 'simple' : 'premium', requirements: copy(data.workspace.requirements), change_summary: change, steps: definitions.map(([key, title, goal, tool, dependencies]) => ({ key, step_id: uid(), title, goal, tool, dependencies: [...dependencies], status: 'pending', tool_call_ids: [] })) };
  if (simple && previous) for (const step of plan.steps) {
    const old = previous.steps.find(candidate => candidate.key === step.key);
    if (old?.status === 'success' && ['materials', 'script'].includes(step.key)) { step.status = 'success'; step.tool_call_ids = [...old.tool_call_ids]; }
  }
  data.workspace.plans.push(plan); publish(id); return plan;
}
async function execute(id: string, resume = false) {
  const data = record(id); let plan = data.workspace.plans.at(-1)!;
  if (!plan) { message(id, '还没有可执行的计划，请先补齐活动需求。'); return; }
  if (['running', 'queued', 'stopping'].includes(data.workspace.active_run?.status || '')) { message(id, '当前计划已经在执行，我会继续更新这一次的进度。'); return; }
  if (data.workspace.active_run?.status === 'completed' && !resume && plan.status === 'completed') { message(id, '这份方案已保存，可以在右侧阅读。若想修改，请直接告诉我。'); return; }
  const runId = resume && data.workspace.active_run ? data.workspace.active_run.run_id : uid();
  data.workspace.active_run = { run_id: runId, status: 'running', phase: 'research', reason: null, plan_version_id: plan.plan_version_id, loaded_skills: ['campaign-planning', 'script-writing'] };
  message(id, resume ? '继续处理未完成步骤，已成功的素材和产物会保留。' : '开始执行这份计划。工具的输入、输出与实际状态会逐项显示在这里。');
  for (const key of ['materials', 'script', 'estimate', 'validate', 'save']) {
    let step = plan.steps.find(item => item.key === key)!;
    if (step.status === 'success') continue;
    const callId = uid(); const call: ToolCall = { tool_call_id: callId, run_id: runId, step_id: step.step_id, tool_name: step.tool, display_name: step.title, server_name: '界面演示 · 非真实 MCP', status: 'running', input: { topic: data.workspace.requirements.topic, production_spec: plan.production_spec }, output: null, started_at: now(), duration_ms: null, error: null, attempt: step.tool_call_ids.length + 1 };
    data.calls[callId] = call; step.tool_call_ids.push(callId); step.status = 'running';
    data.messages.push({ item_id: uid(), item_seq: data.messages.length + 1, kind: 'tool', tool_call_id: callId, created_at: now() }); publish(id);
    await delay(550);
    if (key === 'script' && data.failOnce && !data.failed) {
      data.failed = true; call.status = 'failed'; call.duration_ms = 550; call.error = '演示注入：文案生成暂时失败。'; call.output = { error: call.error }; step.status = 'failed';
      data.workspace.active_run.status = 'incomplete'; data.workspace.active_run.reason = call.error;
      message(id, '文案生成遇到一次演示故障，素材已保留。你可以回复“从失败处继续”。'); return;
    }
    call.status = 'success'; call.duration_ms = 550; step.status = 'success';
    if (key === 'materials') {
      data.workspace.materials = [{ material_id: uid(), title: '内容创意资料：把产品价值变成生活场景', summary: '围绕使用场景、用户困扰与产品回应，组织适合社交平台传播的内容。此条为项目合成资料。', source_type: 'synthetic', url: null, retrieved_at: now() }];
      call.output = { materials: data.workspace.materials };
    } else if (key === 'script') {
      call.output = { script: `## 让好体验，成为日常的一部分\n\n以“${data.workspace.requirements.topic}”为主题，先讲一个真实感的生活场景，再介绍核心价值。\n\n**内容一 · 问题共鸣**\n你是不是也遇到过这样的时刻？从用户的日常困扰切入，用一句简洁的话给出解决方向。\n\n**内容二 · 价值表达**\n用三条清晰、有证据的产品特点回答需求。\n\n**互动收尾**\n告诉我们，你最想改善的日常体验是什么？`, source_ids: data.workspace.materials.map(item => item.material_id) };
    } else if (key === 'estimate') {
      let cost = plan.production_spec === 'simple' ? 8 : 12;
      call.output = { production_cost: String(cost), budget: data.workspace.requirements.budget, unit: '模拟单位', feasible: cost <= Number(data.workspace.requirements.budget) };
      data.workspace.cost_summary.production = String(cost); data.workspace.cost_summary.estimate_stale = false;
      if (cost > Number(data.workspace.requirements.budget)) {
        message(id, `估算结果为 ${cost} 个模拟单位，超过预算。我会改用精简视觉，保留已有文案，并重新估算。`);
        plan = makePlan(id, true, ['视觉制作由高规格调整为精简版', '保留已检索素材与已生成文案', '制作成本重新估算']);
        data.workspace.active_run.plan_version_id = plan.plan_version_id; step = plan.steps.find(item => item.key === key)!;
        const recheckId = uid(); data.calls[recheckId] = { ...copy(call), tool_call_id: recheckId, step_id: step.step_id, input: { production_spec: 'simple' }, output: { production_cost: '8', budget: data.workspace.requirements.budget, unit: '模拟单位', feasible: 8 <= Number(data.workspace.requirements.budget) } };
        step.tool_call_ids.push(recheckId); step.status = 'success'; data.messages.push({ item_id: uid(), item_seq: data.messages.length + 1, kind: 'tool', tool_call_id: recheckId, created_at: now() });
        cost = 8; data.workspace.cost_summary.production = '8';
      }
      if (cost > Number(data.workspace.requirements.budget)) { data.workspace.active_run.status = 'incomplete'; data.workspace.active_run.reason = `当前可行制作规格仍缺 ${cost - Number(data.workspace.requirements.budget)} 个模拟单位。`; message(id, data.workspace.active_run.reason); return; }
      data.workspace.cost_summary.estimated_finish = new Date(Date.now() + 8 * 3600000).toISOString();
    } else if (key === 'validate') call.output = { is_valid: true, missing_fields: [], invalid_references: [] };
    else {
      const proposal: Proposal = { proposal_id: uid(), version: data.workspace.proposals.length + 1, title: data.workspace.requirements.topic || '内容活动方案', saved_at: now(), plan_version_id: plan.plan_version_id, requirements: copy(data.workspace.requirements), production_cost: data.workspace.cost_summary.production || '8', estimated_finish: data.workspace.cost_summary.estimated_finish || now(), materials: copy(data.workspace.materials), run_id: runId,
        markdown: `# ${data.workspace.requirements.topic}\n\n> 界面演示方案：由本地模拟流程生成，未调用真实模型或 MCP。\n\n## 活动目标\n围绕主题建立清晰认知，用有价值的内容提升互动。\n\n## 内容策略\n以场景共鸣、价值说明和互动邀请构成一组连续内容。采用精简视觉，保留核心表达。\n\n## 内容与脚本\n### 第一条：看见日常的小困扰\n从用户熟悉的场景切入，介绍产品如何回应需求。\n\n### 第二条：好体验，有迹可循\n用简洁的语言呈现三项核心价值，并以互动提问收尾。\n\n## 制作排期\n| 制作项 | 工期 |\n| --- | --- |\n| 策划梳理 | 2 小时 |\n| 文案制作 | 3 小时 |\n| 精简视觉 | 3 小时 |\n\n## 制作预算\n| 制作项 | 模拟成本 |\n| --- | --- |\n| 策划 | 1 |\n| 文案 | 2 |\n| 精简视觉 | 5 |\n| **合计** | **8** |\n\n## 素材来源\n使用项目合成资料。所有真实工具调用将在接入后端后另行记录。\n` };
      data.proposals[proposal.proposal_id] = proposal; const { markdown: _, requirements: __, materials: ___, ...ref } = proposal; data.workspace.proposals.push(ref);
      call.output = { proposal_id: proposal.proposal_id, saved: true, mode: 'demo' }; plan.status = 'completed'; data.workspace.active_run.status = 'completed';
    }
    data.workspace.cost_summary.tool_simulated = (Number(data.workspace.cost_summary.tool_simulated) + 0.1).toFixed(2); publish(id);
    if (data.workspace.active_run.control_status) {
      const changes = data.workspace.pending_changes; data.workspace.active_run.status = 'stopped'; data.workspace.active_run.control_status = null;
      if (changes) { parseBrief(id, changes); data.workspace.pending_changes = null; makePlan(id, false, ['已根据新消息更新需求', '保留原运行产物，等待新的执行指令']); message(id, '当前工具已结束。已停止旧计划，并准备好新计划；回复“开始”执行新版。'); }
      else message(id, '当前工具已结束，任务已停止，已有产物保留。');
      publish(id); return;
    }
  }
  message(id, '方案已保存。制作成本为 8 个模拟单位，右侧可以阅读全文、查看版本或导出。');
}

export const demoApi: StudioApi = {
  mode: 'demo',
  async listSessions() { return { items: Object.values(database).map(item => copy(item.workspace.session)).sort((a, b) => b.last_user_activity_at.localeCompare(a.last_user_activity_at)), next_cursor: null, has_more: false }; },
  async createSession() {
    const session: Session = { session_id: uid(), title: '新的内容活动', last_user_activity_at: now(), status: 'draft' };
    const workspace: Workspace = { session, requirements: { topic: null, budget: null, deadline: null, start_at: null, real_sources_required: false }, plans: [], active_run: null, cost_summary: { production: null, budget: null, tool_simulated: '0.00', model_input_tokens: null, model_output_tokens: null, model_cost: null, estimated_finish: null, estimate_stale: false }, materials: [], proposals: [], model_display: '界面演示', mode: 'demo', last_event_seq: 0, pending_changes: null };
    database[session.session_id] = { workspace, messages: [], calls: {}, proposals: {}, clientIds: {}, failOnce: false, failed: false }; publish(session.session_id); return copy(session);
  },
  async getWorkspace(id) { return copy(record(id).workspace); },
  async getMessages(id, cursor) { const all = record(id).messages; const end = cursor ? Number(cursor) : all.length; const start = Math.max(0, end - 30); return { items: copy(all.slice(start, end)), next_cursor: start ? String(start) : null, has_more: start > 0 }; },
  async getToolCall(id) { const call = Object.values(database).map(item => item.calls[id]).find(Boolean); if (!call) throw new Error('找不到工具记录。'); return copy(call); },
  async getProposal(id) { const proposal = Object.values(database).map(item => item.proposals[id]).find(Boolean); if (!proposal) throw new Error('找不到已保存方案。'); return copy(proposal); },
  async sendMessage(id, content, clientId) {
    const data = record(id); if (data.clientIds[clientId]) return { message_id: data.clientIds[clientId] };
    const messageId = message(id, content, 'user'); data.clientIds[clientId] = messageId; data.workspace.session.last_user_activity_at = now();
    setTimeout(() => {
      const active = ['running', 'queued', 'stopping'].includes(data.workspace.active_run?.status || '');
      if (/^(停止|暂停|先停下)/.test(content.trim())) {
        if (active) { data.workspace.active_run!.control_status = 'stop'; data.workspace.active_run!.status = 'stopping'; message(id, '已收到停止请求，等待当前工具结束后保留结果。'); }
        else message(id, '当前没有正在执行的任务。'); return;
      }
      if (active) { data.workspace.pending_changes = content; data.workspace.active_run!.control_status = 'replace'; data.workspace.active_run!.status = 'stopping'; message(id, '已收到修改，当前工具结束后停止旧计划，再生成新版预览。'); return; }
      if (/^(开始(?:执行)?|执行|按这个计划做|按这个方案做)[。！!\s]*$/.test(content.trim())) { void execute(id); return; }
      if (/^(继续|从失败处继续|重试失败步骤|继续执行)[。！!\s]*$/.test(content.trim())) { void execute(id, true); return; }
      if (/先别开始|不要执行|执行过程|解释.*执行/.test(content)) { message(id, '不会开始执行。你可以先查看右侧计划，确认后再通过聊天告诉我。'); return; }
      parseBrief(id, content); const missing = [!data.workspace.requirements.topic && '活动主题', data.workspace.requirements.budget === null && '内容制作预算（模拟单位）', !data.workspace.requirements.deadline && '活动截止日期与时间'].filter(Boolean);
      if (missing.length) message(id, `我已记下这些信息。还需要补充：${missing.join('、')}。\n\n请直接用文字回复，例如“预算 10，2026-12-01 18:00 前完成”。`);
      else { makePlan(id, false, data.workspace.plans.length ? ['已根据新消息更新计划'] : []); message(id, '需求已齐全，我把待执行步骤放在右侧。你可以继续修改，或回复“开始”执行这个计划。'); }
    }, 250);
    return { message_id: messageId };
  },
  subscribe(id, _after, onEvent, onState) { const set = listeners.get(id) || new Set(); set.add(onEvent); listeners.set(id, set); onState('connected'); return () => { set.delete(onEvent); }; },
};
