/** Presentation only: preserve original API values in storage and exported traces. */
export const statusLabels: Record<string, string> = {
  pending: '待执行', queued: '排队中', running: '执行中', success: '已完成', completed: '已完成',
  partial_success: '部分成功', failed: '失败', blocked: '受阻', skipped: '已跳过', needs_clarification: '待确认需求',
  stopping: '停止中', stopped: '已停止', incomplete: '未完成', ready: '待执行指令',
  draft: '收集需求中', superseded: '已被替代',
};
export const statusLabel = (status?: string) => statusLabels[status || ''] || '状态待确认';

export const toolLabels: Record<string, string> = {
  search_synthetic_materials: '合成素材检索', search_web_materials: '真实素材检索',
  generate_script: '文案脚本生成', estimate_proposal: '成本与排期估算',
  validate_proposal: '方案完整性检查', save_proposal: '方案保存',
  load_skill: '任务 Skill 加载', read_skill_resource: 'Skill 参考读取',
};
export const fieldLabels: Record<string, string> = {
  topic: '活动主题', production_spec: '制作规格', budget: '制作预算', production_cost: '制作成本',
  feasible: '预算与排期检查', is_feasible: '预算与排期检查', is_valid: '完整性检查', unit: '计量单位',
  materials: '素材结果', script: '生成脚本', markdown: '方案正文', source_ids: '引用素材编号',
  title: '标题', summary: '摘要', source_type: '来源类型', url: '来源链接', missing_fields: '待补充字段',
  invalid_references: '无效引用', estimated_finish: '预计完成时间', gaps: '缺口', cost_gap: '预算缺口',
  time_gap_hours: '时间缺口（小时）', saved: '保存状态', proposal_id: '方案编号', error: '错误详情',
  query: '检索词', content: '内容', goal: '目标', valid: '检查结果', rules_version: '估算规则版本',
  skill_id: '任务 Skill', version: '版本', sections: '章节', input: '输入', data: '结果',
  source_policy: '素材策略', production_items: '制作项目', tasks: '活动任务', mode: '模式',
  body: 'Skill 正文', resources: '参考资源目录', resource_id: '参考资源标识', description: '用途说明',
  direction: '写作方向', material_id: '素材编号', material_ids: '使用素材编号', retrieved_at: '检索时间',
  model: '生成模型', script_id: '脚本编号', skill_version: 'Skill 版本指纹', hours: '工期（小时）',
  items: '制作明细', cost: '制作成本', name: '制作项目', run_id: '运行编号', deadline: '活动截止时间',
  start_at: '制作开始时间', checked_at: '核验时间', estimate_id: '估算记录编号',
  plan_version_id: '计划版本编号', draft_id: '草稿编号', validation_id: '检查记录编号',
  content_hash: '内容校验指纹', empty: '检索结果', result_unknown: '结果确认状态', reused: '复用状态',
  real_sources_required: '真实来源要求', saved_at: '保存时间', created_at: '创建时间',
  model_input_tokens: '模型输入用量', model_output_tokens: '模型输出用量',
  partial: '结果部分可用', missing: '缺失说明', requested_count: '请求素材数量', available_count: '可用素材数量',
  match_quality: '素材匹配程度', provider: '模型服务', error_code: '错误代码', retryable: '可重试状态',
  error_details: '错误详情', code: '错误代码', message: '错误说明', finish_reason: '模型结束原因',
};
const values: Record<string, Record<string, string>> = {
  production_spec: { premium: '高规格视觉', simple: '精简视觉' },
  source_type: { synthetic: '合成素材', web: '真实来源' },
  skill_id: { 'campaign-planning': '活动策划', 'script-writing': '文案脚本', 'constrained-replanning': '受限重规划', 'proposal-delivery': '方案交付' },
};
export function valueLabel(field: string | undefined, value: string): string {
  return values[field || '']?.[value] || value;
}
export function formatDate(value?: string | null) {
  if (!value) return '待补充';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
}
export const dateFields = new Set(['retrieved_at', 'estimated_finish', 'start_at', 'checked_at', 'deadline', 'saved_at', 'created_at']);
