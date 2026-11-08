import { useQuery } from '@tanstack/react-query';
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined, ClockCircleOutlined, FileTextOutlined, LinkOutlined, ExportOutlined } from '@ant-design/icons';
import { Skeleton, Tag } from 'antd';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Material, Status, StudioApi } from '../types';
import { dateFields, fieldLabels, formatDate, statusLabel, valueLabel } from '../presentation';
export { formatDate, statusLabels, statusLabel } from '../presentation';

export function StatusIcon({ status }: { status: Status | string }) {
  if (status === 'success' || status === 'completed') return <CheckCircleFilled aria-hidden="true" className="status-success" />;
  if (status === 'failed' || status === 'incomplete') return <CloseCircleFilled aria-hidden="true" className="status-failed" />;
  if (status === 'running' || status === 'stopping' || status === 'queued') return <LoadingOutlined aria-hidden="true" className="status-running" />;
  return <ClockCircleOutlined aria-hidden="true" className="status-pending" />;
}
export function Markdown({ children }: { children: string }) {
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: props => <a {...props} target="_blank" rel="noopener noreferrer" />, img: () => null }}>{children}</ReactMarkdown></div>;
}
function ResultValue({ value, depth = 0, field }: { value: unknown; depth?: number; field?: string }) {
  if (value === null || value === undefined) return <span className="muted">{field === 'url' ? '无外部链接' : '未提供'}</span>;
  if (typeof value === 'boolean') {
    const semantic = field === 'is_feasible' || field === 'feasible' ? [value ? '满足预算与截止时间' : '不满足预算或截止时间', value]
      : field === 'is_valid' || field === 'valid' ? [value ? '检查通过' : '检查未通过', value]
      : field === 'saved' ? [value ? '已保存' : '未保存', value]
      : field === 'empty' ? [value ? '未检索到可用素材' : '已检索到可用素材', !value] : null;
    return <span className={semantic ? semantic[1] ? 'status-success' : 'status-failed' : ''}>{semantic ? semantic[0] : value ? '是' : '否'}</span>;
  }
  if (typeof value === 'string') {
    if (dateFields.has(field || '') && !Number.isNaN(Date.parse(value))) return <time dateTime={value} title={`原始时间：${value}`}>{formatDate(value)}（北京时间）</time>;
    if (/^https?:\/\//.test(value)) return <a href={value} target="_blank" rel="noopener noreferrer" className="result-url">{value}</a>;
    return value.includes('\n') ? <Markdown>{value}</Markdown> : <span className="result-text" title={valueLabel(field, value) !== value ? value : undefined}>{valueLabel(field, value)}</span>;
  }
  if (Array.isArray(value)) return value.length ? <div className="result-list">{value.map((item, index) => <div className="result-list-item" key={index}><ResultValue value={item} depth={depth + 1} field={field} /></div>)}</div> : <span className="muted">无</span>;
  if (typeof value === 'object' && depth < 8) return <dl className="result-object">{Object.entries(value).map(([key, item]) => <div key={key}><dt title={key} data-field-key={key} data-untranslated={fieldLabels[key] ? undefined : 'true'}>{fieldLabels[key] || key}</dt><dd><ResultValue value={item} depth={depth + 1} field={key} /></dd></div>)}</dl>;
  if (typeof value === 'object') return <pre>{JSON.stringify(value, null, 2)}</pre>;
  return <span>{String(value)}</span>;
}
export function ToolCard({ id, api, onLocate, stepIds }: { id: string; api: StudioApi; onLocate: (stepId: string) => void; stepIds: string[] }) {
  const { data: call, error } = useQuery({ queryKey: [api.mode, 'tool', id], queryFn: () => api.getToolCall(id) });
  if (error) return <div className="tool-card tool-failed">工具记录读取失败：{error.message}</div>;
  if (!call) return <div className="tool-card"><Skeleton active paragraph={{ rows: 2 }} /></div>;
  const linkedStep=call.reused_in_step_ids?.find(step=>stepIds.includes(step)) || (stepIds.includes(call.step_id) ? call.step_id : undefined);
  return <article className={`tool-card tool-${call.status}`} id={`call-${id}`} data-testid="tool-card">
    <div className="tool-card-top"><span className="tool-icon"><FileTextOutlined /></span><strong>{call.display_name}</strong><span className={`tool-state state-${call.status}`} title={call.status}><StatusIcon status={call.status} /> {statusLabel(call.status)}</span></div>
    <div className="tool-section"><span className="tool-section-label">输入 <small>INPUT</small></span><ResultValue value={call.input} /></div>
    <div className="tool-section"><span className="tool-section-label">输出 <small>OUTPUT</small></span>{call.output == null ? <span className="muted">{call.status === 'running' || call.status === 'queued' ? '等待工具返回…' : call.status === 'failed' ? '未取得成功结果' : '未提供输出'}</span> : <ResultValue value={call.output} />}{call.error && <p className="tool-error">{call.error}</p>}</div>
    <div className="tool-card-foot"><span>{call.duration_ms === null ? call.status === 'running' || call.status === 'queued' ? '正在处理' : '耗时未提供' : `${(call.duration_ms / 1000).toFixed(1)} 秒`}<i />第 {call.attempt} 次尝试{linkedStep && linkedStep!==call.step_id ? ['success', 'partial_success'].includes(call.status) ? ' · 复用历史结果' : call.status === 'failed' ? ' · 保留失败记录' : ' · 历史尝试记录' : ''}</span>{linkedStep ? <button className="text-action" onClick={() => onLocate(linkedStep)}>查看对应步骤 <ExportOutlined /></button> : <span>{call.run_id ? '历史运行记录' : '对话准备 · 无执行步骤'}</span>}</div>
  </article>;
}
export function MaterialCard({ material }: { material: Material }) {
  return <article className="material-card"><div className="material-top"><span className="material-icon"><FileTextOutlined /></span><Tag className={`source-tag source-${material.source_type}`} color={material.source_type === 'web' ? 'green' : 'default'}>{material.source_type === 'web' ? '真实来源' : '合成素材'}</Tag></div><h4>{material.title}</h4><p>{material.summary}</p>{material.url ? <a className="source-link" href={material.url} target="_blank" rel="noopener noreferrer"><LinkOutlined /> 查看来源 <ExportOutlined /></a> : <span className="source-caption">项目资料库 · 合成内容</span>}</article>;
}
export function EmptySection({ title, description }: { title: string; description: string }) {
  return <div className="section-empty"><span className="empty-line" /><strong>{title}</strong><p>{description}</p></div>;
}
