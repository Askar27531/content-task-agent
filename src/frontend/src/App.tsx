import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { Alert, App as AntApp, Button, Collapse, Drawer, Input, Modal, Select, Spin, Tooltip } from 'antd';
import { ArrowDownOutlined, ArrowRightOutlined, ArrowUpOutlined, CheckOutlined, CloseOutlined, CopyOutlined, DownloadOutlined, ExpandOutlined, FileTextOutlined, FormOutlined, HistoryOutlined, LoadingOutlined, MenuOutlined, PlusOutlined, RedoOutlined } from '@ant-design/icons';
import { demoApi } from './api/demo';
import { toolLabels } from './presentation';
import { httpApi } from './api/http';
import { EmptySection, formatDate, Markdown, MaterialCard, StatusIcon, statusLabel, ToolCard } from './components/Content';
import type { Connection, Plan, Proposal, StudioApi, StudioEvent, TimelineItem, ViewContext, Workspace } from './types';

function Brand() { return <span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none"><path d="M7 7h10v6H7zM7 17h6v8H7zM17 17h8v8h-8z" fill="currentColor" /><path d="M21 5v10M16 10h10" stroke="currentColor" strokeWidth="2.5" /></svg></span>; }
function History({ api, activeId, onSelect, onCreate }: { api: StudioApi; activeId?: string; onSelect: (id: string) => void; onCreate: () => void }) {
  const sessions = useQuery({ queryKey: [api.mode, 'sessions'], queryFn: () => api.listSessions() });
  return <div className="history-content"><div className="brand"><Brand /><div><strong>内容工坊</strong><span>CONTENT STUDIO</span></div></div><Button className="new-session" aria-label="新建会话" icon={<PlusOutlined aria-hidden="true" />} onClick={onCreate}>新建会话</Button><div className="history-label">最近的活动 <span>{sessions.data?.items.length || '—'}</span></div><div className="history-list">{sessions.data?.items.map(session => <button key={session.session_id} className={`history-item ${session.session_id === activeId ? 'selected' : ''}`} onClick={() => onSelect(session.session_id)} title={session.title}><FileTextOutlined /><span><strong>{session.title}</strong><small>{formatDate(session.last_user_activity_at)}</small></span>{session.session_id === activeId && <span className="selected-dot" />}</button>)}{!sessions.data?.items.length && <div className="history-empty"><HistoryOutlined /><p>还没有活动记录</p><span>新的想法，从这里开始</span></div>}</div><div className="sidebar-bottom"><span className="tiny-spark">✳</span><p>把每一个好想法，<br />变成可执行的内容。</p><span className="sidebar-version">LOCAL WORKSPACE <i /> v0.1</span></div></div>;
}

function useWorkspace(api: StudioApi, id?: string) {
  const client = useQueryClient(); const [connection, setConnection] = useState<Connection>('connecting');
  const workspace = useQuery({ queryKey: [api.mode, 'workspace', id], queryFn: () => api.getWorkspace(id!), enabled: !!id });
  const timeline = useInfiniteQuery({ queryKey: [api.mode, 'messages', id], queryFn: ({ pageParam }) => api.getMessages(id!, pageParam), initialPageParam: undefined as string | undefined, getNextPageParam: last => last.next_cursor || undefined, enabled: !!id });
  const ready = !!workspace.data;
  useEffect(() => {
    if (!id || !ready) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastSeq = workspace.data?.last_event_seq || 0;
    const handle = (event: StudioEvent) => {
      if (event.session_seq <= lastSeq && event.type !== 'resync_required') return;
      lastSeq = event.session_seq;
      if (timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        void client.invalidateQueries({ queryKey: [api.mode, 'workspace', id] });
        void client.invalidateQueries({ queryKey: [api.mode, 'messages', id] });
        void client.invalidateQueries({ queryKey: [api.mode, 'sessions'] });
        void client.invalidateQueries({ queryKey: [api.mode, 'tool'] });
        void client.invalidateQueries({ queryKey: [api.mode, 'proposal'] });
      }, 120);
    };
    const close = api.subscribe(id, lastSeq, handle, setConnection);
    return () => { close(); if (timer) clearTimeout(timer); };
    // A snapshot update must not open a second SSE connection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, id, ready, client]);
  const items = [...new Map(timeline.data?.pages.flatMap(page => page.items).map(item => [item.item_id, item]) || []).values()].sort((a, b) => a.item_seq - b.item_seq);
  return { workspace, timeline, items, connection };
}

function DocumentPanel({ workspace, planId, onPlanChange, api, onLocateCall }: { workspace?: Workspace; planId?: string; onPlanChange: (id?: string) => void; api: StudioApi; onLocateCall: (id: string) => void }) {
  const { message } = AntApp.useApp();
  const [activeAnchor, setActiveAnchor] = useState('overview');
  const [proposalId, setProposalId] = useState<string>(); const [enlarged, setEnlarged] = useState(false);
  const followsLatest = useRef(true); const panel = useRef<HTMLDivElement>(null);
  const latestProposal = workspace?.proposals.at(-1);
  useEffect(() => { if (latestProposal && followsLatest.current) setProposalId(latestProposal.proposal_id); }, [latestProposal?.proposal_id]);
  const proposal = useQuery({ queryKey: [api.mode, 'proposal', proposalId], queryFn: () => api.getProposal(proposalId!), enabled: !!proposalId });
  const latestPlan = workspace?.plans.at(-1); const plan = workspace?.plans.find(item => item.plan_version_id === planId) || latestPlan;
  const historical = !!planId && planId !== latestPlan?.plan_version_id;
  const brief = historical && plan ? plan.requirements : workspace?.requirements;
  const costs = workspace?.cost_summary;
  const estimateCallId = plan?.steps.find(step => step.key === 'estimate')?.tool_call_ids.at(-1);
  const historicalEstimate = useQuery({ queryKey: [api.mode, 'tool', estimateCallId], queryFn: () => api.getToolCall(estimateCallId!), enabled: historical && !!estimateCallId });
  const observed = historicalEstimate.data?.status === 'success' ? historicalEstimate.data.output as { production_cost?: string; estimated_finish?: string } | null : null;
  const costValue = historical ? plan?.estimated_cost ?? observed?.production_cost : costs?.production;
  const estimatedFinish = historical ? observed?.estimated_finish : costs?.estimated_finish;
  const estimateStale = !historical && costs?.estimate_stale;
  const proposalPlan = workspace?.plans.find(item => item.plan_version_id === proposal.data?.plan_version_id);
  const proposalOutdated = !!(proposal.data && brief && (['topic', 'budget', 'deadline', 'start_at', 'real_sources_required'] as const).some(key => proposal.data!.requirements[key] !== brief[key]));
  const proposalMismatch = proposalOutdated || !!(plan && proposal.data && plan.plan_version_id !== proposal.data.plan_version_id);
  const budget = brief?.budget;
  const ratio = costValue != null && budget != null ? (Number(budget) ? Number(costValue) / Number(budget) : Number(costValue) ? 2 : 0) : 0;
  const materials = proposal.data && !followsLatest.current ? proposal.data.materials : workspace?.materials || [];
  function jump(id: string) { setActiveAnchor(id); const element = panel.current?.querySelector<HTMLElement>(`#doc-${id}`); if (element && panel.current) panel.current.scrollTo({ top: element.offsetTop - panel.current.offsetTop - 20, behavior: 'smooth' }); }
  async function download(format: 'markdown' | 'json') {
    if (!proposal.data) return;
    const value = format === 'markdown' ? proposal.data.markdown : JSON.stringify(proposal.data, null, 2);
    const url = URL.createObjectURL(new Blob([value], { type: format === 'markdown' ? 'text/markdown;charset=utf-8' : 'application/json;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `${proposal.data.title.slice(0, 30).replace(/[\\/:*?"<>|]/g, '-')}-v${proposal.data.version}.${format === 'markdown' ? 'md' : 'json'}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const exportActions = <div className="document-actions"><Tooltip title="复制全文"><Button size="small" type="text" icon={<CopyOutlined />} aria-label="复制方案" onClick={() => { void navigator.clipboard.writeText(proposal.data?.markdown || '').then(() => message.success('已复制方案')).catch(() => message.error('复制未完成')); }} /></Tooltip><Tooltip title="下载 Markdown"><Button size="small" type="text" icon={<DownloadOutlined />} aria-label="下载 Markdown" onClick={() => void download('markdown')} /></Tooltip><Button size="small" type="text" onClick={() => void download('json')}>JSON</Button><Tooltip title="放大阅读"><Button size="small" type="text" icon={<ExpandOutlined />} aria-label="放大阅读" onClick={() => setEnlarged(true)} /></Tooltip></div>;
  return <>
    <div className="document-heading"><span><FileTextOutlined /> 活动方案</span><span className="micro-label">从想法，到落地</span></div>
    <nav className="document-nav" aria-label="方案分区">{[['overview', '概览'], ['costs', '费用'], ['plan', '计划'], ['proposal', '方案'], ['materials', '素材']].map(([id, label]) => <button key={id} className={activeAnchor === id ? 'active' : ''} onClick={() => jump(id)}>{label}</button>)}</nav>
    <div className="document-scroll" ref={panel}>
      <section id="doc-overview" className="document-section overview-section">
        <div className="section-kicker">THE BRIEF <span>01</span></div><h2>活动概览</h2>
        {historical && <div className="historical-notice">正在查看历史计划 v{plan?.version}<button className="text-action" onClick={() => onPlanChange(undefined)}>返回最新</button></div>}
        {!!workspace?.requirements_issues?.length && workspace.last_event_seq > 0 && <Alert className="requirements-issues" title="待确认事项" description={<ul>{workspace.requirements_issues.map(issue => <li key={issue}>{issue}</li>)}</ul>} type="warning" showIcon />}
        {workspace?.pending_changes && <Alert title="待应用的修改" description={workspace.pending_changes} type="info" showIcon />}
        <div className="brief-card"><div className="brief-topic"><span className="field-label">活动主题</span><h3 className={!brief?.topic ? 'placeholder' : ''}>{brief?.topic || '等待你的第一个想法'}</h3></div><div className="brief-grid"><div><span className="field-label">制作预算</span><strong className={budget == null ? 'placeholder' : ''}>{budget == null ? '待补充' : budget}<small>{budget == null ? '' : ' 模拟单位'}</small></strong></div><div><span className="field-label">活动截止时间</span><strong className={!brief?.deadline ? 'placeholder' : ''}>{formatDate(brief?.deadline)}</strong></div></div><div className="brief-foot"><span className="soft-dot" />{brief?.real_sources_required ? '仅使用真实素材' : '可使用合成资料与真实来源'}<span>北京时间</span></div></div>
      </section>
      <section id="doc-costs" className="document-section costs-section">
        <div className="budget-heading"><span>制作成本 <small>/ 预算</small></span><span className={costValue == null || estimateStale ? 'budget-note budget-pending' : ratio > 1 ? 'over-budget-label' : 'budget-note'}>{costValue == null ? '待估算' : estimateStale ? '待重新估算' : ratio > 1 ? '超出预算' : '预算内'}</span></div>
        <div className="budget-numbers"><strong className={ratio > 1 ? 'status-failed' : ''}>{costValue ?? '—'}</strong><span>/ {budget ?? '—'}</span><small>模拟单位</small></div>
        <div className="budget-track"><span className={ratio > 1 ? 'over-budget' : ''} style={{ width: `${Math.min(100, ratio * 100)}%` }} /></div>
        <p className="budget-caption">{historical ? `所选历史计划 v${plan?.version} 的制作估算；工具开销为当前运行累计。` : estimateStale ? '显示上次制作估算，新计划仍待核验；工具与模型开销另列。' : '仅核算方案制作成本，工具与模型开销另列。'}</p>
        <Collapse ghost className="cost-details" items={[{ key: 'costs', label: '查看工具与模型开销', children: <dl className="cost-list"><div><dt>当前运行工具模拟开销</dt><dd>{workspace?.active_run ? `${costs?.tool_simulated || '0.00'} 模拟单位` : '尚无业务调用'}</dd></div><div><dt>本会话已知模型输入 / 输出</dt><dd>{costs?.model_input_tokens == null ? '未提供用量' : `${costs.model_input_tokens} / ${costs.model_output_tokens ?? '未知'} tokens`}</dd></div><div><dt>模型 API 费用</dt><dd>{costs?.model_cost == null ? '未提供计价' : `${costs.model_cost} ${costs.model_cost_currency || ''}（按配置估算）`}</dd></div><div><dt>{historical ? '所选计划预计完成' : '预计活动完成'}</dt><dd>{estimatedFinish ? formatDate(estimatedFinish) : historical ? '该版本尚无有效排期' : '待估算'}</dd></div>{!!costs?.model_unknown_usage_calls && <div><dt>用量缺失请求</dt><dd>{costs.model_unknown_usage_calls} 次（未计入已知用量）</dd></div>}</dl> }]} />
      </section>
      <section id="doc-plan" className="document-section"><div className="section-kicker">THE PROCESS <span>02</span></div><div className="section-title-row"><h2>任务计划</h2>{workspace?.plans.length ? <Select aria-label="计划版本" virtual={false} popupMatchSelectWidth={false} size="small" value={plan?.plan_version_id} onChange={id => onPlanChange(id === latestPlan?.plan_version_id ? undefined : id)} options={workspace.plans.map(item => ({ value: item.plan_version_id, label: `v${item.version} · ${item.label}` }))} /> : <span className="neutral-tag">待生成</span>}</div>
        {plan ? <><p className="section-description">{plan.label} · {plan.steps.length} 个步骤{plan.status === 'ready' && !historical ? ' · 等待聊天执行指令' : ''}<span className={`plan-state state-${plan.status}`} data-testid="plan-status">{statusLabel(plan.status)}</span></p>{plan.change_summary.length > 0 && <div className="change-summary"><strong>本次调整</strong>{plan.change_summary.map((change, index) => <p key={index}>{change}</p>)}</div>}<div className="steps">{plan.steps.map((step, index) => <details className={`plan-step step-${step.status}`} id={`step-${step.step_id}`} key={step.step_id}><summary><span className="step-number">{step.status === 'success' ? <CheckOutlined /> : String(index + 1).padStart(2, '0')}</span><span className="step-title">{step.title}<small title={step.tool}>{toolLabels[step.tool] || '工具信息待确认'}</small></span><span className="step-status"><StatusIcon status={step.status} /><span>{statusLabel(step.status)}</span></span></summary><div className="step-detail"><p>{step.goal}</p>{step.blocked_reason && <p className="blocked-reason">受阻原因：{step.blocked_reason}</p>}<p>工具标识：<code>{step.tool}</code></p><p>依赖：{step.dependencies.length ? step.dependencies.map(key => plan.steps.find(item => item.key === key)?.title || key).join('、') : '无前置依赖'}</p>{step.tool_call_ids.map((callId, number) => <button className="text-action" onClick={() => onLocateCall(callId)} key={callId}>查看第 {number + 1} 次调用记录 ↗</button>)}</div></details>)}</div>{plan.status === 'ready' && !historical && <div className="chat-instruction">在聊天中回复 <strong>“开始”</strong> 即可执行，也可以继续提出修改。</div>}</> : <EmptySection title="清晰的计划，从完整的需求开始" description="告诉我主题、预算和活动截止时间，步骤会在这里逐项展开。" />}
      </section>
      {workspace?.active_run?.reason && <div className="failure-summary"><StatusIcon status={workspace.active_run.status} /><div><strong>{statusLabel(workspace.active_run.status)}</strong><p>{workspace.active_run.reason}</p><small>已成功的结果会保留。你可以在聊天中继续处理。</small></div></div>}
      <section id="doc-proposal" className="document-section"><div className="section-kicker">THE DELIVERABLE <span>03</span></div><div className="section-title-row"><h2>完整方案</h2>{proposal.data && exportActions}</div>
        {workspace?.proposals.length ? <div className="proposal-version"><Select aria-label="方案版本" virtual={false} popupMatchSelectWidth={false} size="small" value={proposalId} onChange={id => { followsLatest.current = id === latestProposal?.proposal_id; setProposalId(id); const ref = workspace.proposals.find(item => item.proposal_id === id); onPlanChange(ref?.plan_version_id === latestPlan?.plan_version_id ? undefined : ref?.plan_version_id); }} options={workspace.proposals.map(item => ({ value: item.proposal_id, label: `方案 v${item.version} · ${formatDate(item.saved_at)}` }))} /><span className="saved-note"><CheckOutlined /> 已保存 · 来自计划 v{proposalPlan?.version ?? '—'}</span></div> : null}
        {proposalMismatch && <div className="version-scope-notice" role="note">{proposalOutdated ? `下方是之前需求的已保存方案 v${proposal.data?.version}；当前需求尚未保存新方案。` : `下方是已保存方案 v${proposal.data?.version}，来自计划 v${proposalPlan?.version ?? '—'}；与当前查看的计划 v${plan?.version} 不同。`}</div>}{proposal.data ? <><div className="proposal-paper"><Markdown>{proposal.data.markdown}</Markdown></div><p className="saved-caption">保存于 {formatDate(proposal.data.saved_at)} · 方案 v{proposal.data.version}</p></> : proposal.isLoading ? <div className="section-empty"><Spin /></div> : <EmptySection title="这里，将是一份可执行的方案" description="执行完成并保存后，你可以在这里阅读全文、追溯来源与导出。" />}
      </section>
      <section id="doc-materials" className="document-section materials-section"><div className="section-kicker">THE SOURCES <span>04</span></div><div className="section-title-row"><h2>素材与来源</h2><span className="neutral-tag">{materials.length} 项</span></div>{materials.length ? materials.map(item => <MaterialCard material={item} key={item.material_id} />) : <EmptySection title="让内容有据可依" description="检索到的素材将在此保留摘要与来源，真实和合成资料会明确区分。" />}</section>
      <footer className="document-footer">CONTENT STUDIO <span>每个决定，都有迹可循。</span></footer>
    </div>
    <Modal open={enlarged} onCancel={() => setEnlarged(false)} footer={null} width="min(960px, 94vw)" title={<div className="reader-heading"><span>方案 v{proposal.data?.version}</span><span>{proposal.data?.title}</span></div>} className="reader-modal"><div className="reader-body"><Markdown>{proposal.data?.markdown || ''}</Markdown></div></Modal>
  </>;
}

function Studio() {
  const { message } = AntApp.useApp();
  const location = useLocation(); const navigate = useNavigate(); const client = useQueryClient();
  const isDemo = location.pathname.startsWith('/demo'); const api = isDemo ? demoApi : httpApi;
  const id = location.pathname.match(/\/sessions\/([^/]+)/)?.[1]; const prefix = isDemo ? '/demo' : '';
  const { workspace: query, timeline, items, connection } = useWorkspace(api, id); const workspace = query.data;
  const [drawer, setDrawer] = useState(false); const [mobileView, setMobileView] = useState('chat'); const [selectedPlan, setSelectedPlan] = useState<string>();
  const [drafts, setDrafts] = useState<Record<string, string>>({}); const [sending, setSending] = useState(false); const [creating, setCreating] = useState(false);
  const [failed, setFailed] = useState<{content: string; clientId: string; sessionId: string; error: string}[]>([]);
  const [newContent, setNewContent] = useState(false); const scroll = useRef<HTMLDivElement>(null); const content = useRef<HTMLDivElement>(null); const follow = useRef(true); const userScrollUntil = useRef(0);
  const draftKey = id || 'new'; const draft = drafts[draftKey] || '';
  const setDraft = (value: string) => setDrafts(previous => ({ ...previous, [draftKey]: value }));
  useEffect(() => { setSelectedPlan(undefined); follow.current = true; setNewContent(false); }, [id]);
  useLayoutEffect(() => {
    if (!content.current) return;
    const observer = new ResizeObserver(() => { if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; else if (items.length) setNewContent(true); });
    observer.observe(content.current); return () => observer.disconnect();
  }, [id, items.length]);
  useEffect(() => { if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; else if (items.length) setNewContent(true); }, [workspace?.last_event_seq, items.length]);
  async function create() {
    if (creating) return; setCreating(true);
    try { const session = await api.createSession(); await client.invalidateQueries({ queryKey: [api.mode, 'sessions'] }); navigate(`${prefix}/sessions/${session.session_id}`); setDrawer(false); return session.session_id; }
    catch (error) { message.error((error as Error).message); return undefined; } finally { setCreating(false); }
  }
  async function send(retry?: {content: string; clientId: string; sessionId: string}) {
    const text = (retry?.content ?? draft).trim(); if (!text || creating) return; if (Array.from(text).length > 20000) { message.warning('消息内容不能超过20000个字符，请精简后再发送。'); return; }
    setSending(true); const clientId = retry?.clientId || crypto.randomUUID(); let sessionId = retry?.sessionId || id;
    try {
      if (!sessionId) sessionId = await create(); if (!sessionId) return;
      const context: ViewContext = { plan_version_id: selectedPlan || workspace?.plans.at(-1)?.plan_version_id };
      await api.sendMessage(sessionId, text, clientId, context);
      if (!retry) setDrafts(previous => ({ ...previous, [draftKey]: '' }));
      setFailed(previous => previous.filter(item => item.clientId !== clientId)); follow.current = true;
      await Promise.all([client.invalidateQueries({ queryKey: [api.mode, 'messages', sessionId] }), client.invalidateQueries({ queryKey: [api.mode, 'workspace', sessionId] }), client.invalidateQueries({ queryKey: [api.mode, 'sessions'] })]);
    } catch (error) { const failedSessionId = sessionId; if (failedSessionId) setFailed(previous => [...previous.filter(item => item.clientId !== clientId), { content: text, clientId, sessionId: failedSessionId, error: (error as Error).message }]); else message.error((error as Error).message); }
    finally { setSending(false); }
  }
  function locateCall(callId: string) { setMobileView('chat'); setTimeout(() => { const element = document.getElementById(`call-${callId}`); if (element) { follow.current = false; element.scrollIntoView({ block: 'center', behavior: 'smooth' }); element.classList.add('locate-flash'); setTimeout(() => element.classList.remove('locate-flash'), 1800); } else message.info('这条记录在更早的对话中，请先加载历史消息。'); }, 30); }
  function locateStep(stepId: string) { const sourcePlan = workspace?.plans.find(plan => plan.steps.some(step => step.step_id === stepId)); if (sourcePlan) setSelectedPlan(sourcePlan.plan_version_id === workspace?.plans.at(-1)?.plan_version_id ? undefined : sourcePlan.plan_version_id); setMobileView('document'); setTimeout(() => { const element = document.getElementById(`step-${stepId}`) as HTMLDetailsElement | null; if (element) { element.open = true; element.scrollIntoView({ block: 'center', behavior: 'smooth' }); element.classList.add('locate-flash'); setTimeout(() => element.classList.remove('locate-flash'), 1800); } }, 60); }
  const latestPlan = workspace?.plans.at(-1); const run = workspace?.active_run;
  const status = workspace?.requirements_issues?.length && workspace.last_event_seq > 0 && !['running', 'queued', 'stopping'].includes(run?.status || '') ? 'needs_clarification' : latestPlan?.status === 'ready' && !['running', 'queued', 'stopping'].includes(run?.status || '') ? 'ready' : run?.status || (workspace?.requirements.topic ? 'draft' : 'draft');
  const model = isDemo ? '界面演示 · 未调用模型' : workspace?.model_display || '本地 Qwen3';
  const allFailed = failed.filter(item => item.sessionId === id);
  const select = (sessionId: string) => { navigate(`${prefix}/sessions/${sessionId}`); setDrawer(false); };
  return <div className="studio-app" data-session-id={workspace?.session.session_id}>
    <aside className="desktop-sidebar"><History api={api} activeId={id} onSelect={select} onCreate={() => void create()} /></aside>
    <main className="studio-main"><header className="app-header"><div className="header-left"><button className="history-toggle" onClick={() => setDrawer(true)} aria-label="打开历史会话"><MenuOutlined /></button><span className="header-breadcrumb">工作空间 <span>/</span> <strong>{workspace?.session.title || '新的内容活动'}</strong></span></div><div className="header-right">{isDemo && <span className="mode-label">演示数据</span>}<span className="model-label" title={model}><span className="model-dot" />{model}</span><span className="local-label">LOCAL</span></div></header>
      {!isDemo && id && ['reconnecting', 'offline'].includes(connection) && <div className="connection-banner"><LoadingOutlined />连接暂时中断，正在恢复更新。后台任务会继续，最近同步结果已保留。</div>}
      <div className="mobile-switch"><button className={mobileView === 'chat' ? 'active' : ''} onClick={() => setMobileView('chat')}>对话</button><button className={mobileView === 'document' ? 'active' : ''} onClick={() => setMobileView('document')}>方案</button></div>
      <div className="workspace-grid"><section className={`chat-pane ${mobileView === 'chat' ? 'mobile-active' : ''}`} aria-label="活动对话"><div className="chat-heading"><span>对话 <small>让想法逐渐清晰</small></span><span className={`workspace-status state-${status}`}><span className="soft-dot" />{statusLabel(status)}</span></div>
        <div className="chat-scroll" ref={scroll} onWheel={() => { userScrollUntil.current = Date.now() + 250; }} onTouchMove={() => { userScrollUntil.current = Date.now() + 250; }} onPointerDown={event => { if (event.target === event.currentTarget) userScrollUntil.current = Date.now() + 400; }} onScroll={() => { const element = scroll.current!; const bottom = element.scrollHeight - element.scrollTop - element.clientHeight < 75; if (bottom) { follow.current = true; setNewContent(false); } else if (Date.now() < userScrollUntil.current) follow.current = false; }}>
          <div className="chat-content" ref={content}>
            {timeline.hasNextPage && <button className="load-history" onClick={() => { follow.current = false; void timeline.fetchNextPage(); }}>加载更早的对话</button>}
            {!items.length && !query.error && <div className="chat-welcome"><span className="welcome-symbol">✳</span><span className="section-kicker">A GOOD IDEA STARTS HERE</span><h1>下一场好活动，<br />从一个想法开始。</h1><p>告诉我你想做什么。<br />我们一起把主题、预算和时间，<br />变成一份有据可循的内容方案。</p><div className="welcome-note"><span>你可以这样说</span><p>“为新品做一次内容推广，<br />预算 10，2026-12-01 18:00 前完成。”</p></div><div className="welcome-capabilities"><span>需求梳理</span><i /><span>动态规划</span><i /><span>预算与排期</span></div></div>}
            {query.error && <div className="backend-error"><FileTextOutlined /><h3>工作空间暂时未连接</h3><p>{query.error.message}</p><Button icon={<RedoOutlined />} onClick={() => void query.refetch()}>重新连接</Button><a href="/demo">查看独立的界面演示 ↗</a></div>}
            {items.map(item => item.kind === 'tool' ? <div className="tool-message" key={item.item_id}><ToolCard id={item.tool_call_id!} api={api} onLocate={locateStep} stepIds={(workspace?.plans.find(plan => plan.plan_version_id === selectedPlan) || workspace?.plans.at(-1))?.steps.map(step => step.step_id) || []} /></div> : <div className={`chat-message message-${item.role}`} key={item.item_id}><div className="message-label">{item.role === 'assistant' ? <><span className="assistant-symbol">✳</span> 内容工坊</> : '你'}<time>{formatDate(item.created_at).split(' ').at(-1)}</time></div><div className="message-body"><Markdown>{item.content || ''}</Markdown>{item.processing && <span className="typing-indicator"><i /><i /><i /></span>}</div></div>)}
            {allFailed.map(item => <div className="chat-message message-user send-failed" key={item.clientId}><div className="message-body">{item.content}</div><p>发送失败 · {item.error}<button className="text-action" onClick={() => void send(item)}>重发</button></p></div>)}
          </div>
          {newContent && <button className="new-content" onClick={() => { follow.current = true; scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' }); setNewContent(false); }}>有新内容 <ArrowDownOutlined /></button>}
        </div>
        <div className="composer-area"><div className="composer-box"><Input.TextArea aria-label="输入活动需求" value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send(); } }} placeholder="描述你的活动想法，或继续告诉我…" autoSize={{ minRows: 2, maxRows: 6 }} variant="borderless" /><div className="composer-bottom"><span><FormOutlined /> 通过对话规划与执行</span><Button type="primary" className="send-button" aria-label="发送消息" onClick={() => void send()} disabled={!draft.trim() || creating} icon={sending ? <LoadingOutlined /> : <ArrowUpOutlined />} /></div></div><div className="composer-caption"><span>Enter 发送 <span>·</span> Shift + Enter 换行</span><span>执行、续跑、停止，请直接说。</span></div></div>
      </section><section className={`document-pane ${mobileView === 'document' ? 'mobile-active' : ''}`} aria-label="活动方案"><DocumentPanel key={id || 'new'} workspace={workspace} planId={selectedPlan} onPlanChange={setSelectedPlan} api={api} onLocateCall={locateCall} /></section></div>
    </main>
    <Drawer open={drawer} onClose={() => setDrawer(false)} placement="left" size={280} title={null} className="history-drawer" closeIcon={<CloseOutlined />}><History api={api} activeId={id} onSelect={select} onCreate={() => void create()} /></Drawer>
  </div>;
}
export default function App() { return <AntApp><Studio /></AntApp>; }


