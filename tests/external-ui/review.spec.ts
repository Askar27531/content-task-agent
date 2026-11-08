import { expect, test } from '@playwright/test';
import fs from 'node:fs';
const root='examples/external-live/after';
const evidence=(name:string)=>JSON.parse(fs.readFileSync(`${root}/${name}/evidence.json`,'utf8'));
test.beforeEach(()=>fs.mkdirSync(`${root}/ui/screenshots`,{recursive:true}));

test('real automatic fault and 12 to 8 replan keep failed attempt and show final valid steps',async({page,request})=>{
  const item=evidence('audit-auto-failure-replan');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  await expect(page.locator('.budget-numbers>strong')).toHaveText('8');
  await expect(page.locator('.budget-numbers')).toContainText('/ 10');
  await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  const failed=item.tool_calls.find((o:{tool_name:string,status:string})=>o.tool_name==='generate_script'&&o.status==='failed');
  const card=page.locator(`#call-${failed.tool_call_id}`);
  const history=page.getByRole('button',{name:/加载更早/});
  for(let i=0;i<4&&!await card.count()&&await history.count();i++)await history.click();
  await expect(card.locator('.tool-state')).toHaveText(/失败/);
  await expect(card.locator('.tool-card-foot')).toContainText('保留失败记录');
  await expect(card.locator('.tool-card-foot')).not.toContainText('复用历史结果');
  await expect(page.locator('.failure-summary')).toHaveCount(0);
  await expect(page.locator('.material-card .source-tag')).toHaveText(item.state.materials.map(()=> '真实来源'));
  const ref=item.state.proposals[0];const saved=await (await request.get(`/api/proposals/${ref.proposal_id}`)).json();
  expect(saved.production_cost).toBe('8');await expect(page.locator('.proposal-paper')).toContainText(saved.title);
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({path:`${root}/ui/screenshots/auto-failure-replan.png`});
});

test('the old premium plan still shows its actual over-budget estimate',async({page})=>{
  const item=evidence('audit-auto-failure-replan');await page.goto(`/sessions/${item.session_id}`);
  await page.locator('#doc-plan .ant-select').click();
  await page.getByText(`v1 · ${item.state.plans[0].label}`,{exact:true}).click();
  await expect(page.locator('.historical-notice')).toContainText('历史计划 v1');
  await expect(page.locator('.budget-numbers>strong')).toHaveText('12');
  await expect(page.locator('.over-budget-label')).toHaveText('超出预算');
  await expect(page.locator('[data-testid="plan-status"]')).toHaveText('已被替代');
  await page.screenshot({path:`${root}/ui/screenshots/premium-history.png`});
});

test('real-source script failure resumes with retained sources and actual saved document',async({page})=>{
  const item=evidence('combined-failure-resume');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  await expect(page.locator('.material-card')).toHaveCount(item.before.materials.length);
  await expect(page.locator('.plan-step .blocked-reason')).toHaveCount(0);
  await expect(page.locator('.proposal-paper')).toBeVisible();
});

test('actual client timeout remains failed and strict source requirement does not fabricate a result',async({page})=>{
  const item=evidence('tavily-timeout-strict');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('未完成');
  await expect(page.locator('.material-card')).toHaveCount(0);
  await expect(page.locator('.proposal-paper')).toHaveCount(0);
  await expect(page.locator('.tool-card.tool-failed')).not.toHaveCount(0);
  await expect(page.locator('.failure-summary')).toBeVisible();
  await page.screenshot({path:`${root}/ui/screenshots/strict-timeout.png`});
});

test('real search timeout fallback is visibly synthetic and saved with provenance warning',async({page,request})=>{
  const item=evidence('tavily-timeout-fallback');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  await expect(page.locator('.material-card .source-tag')).toHaveText(item.state.materials.map(()=> '合成素材'));
  const saved=await (await request.get(`/api/proposals/${item.state.proposals[0].proposal_id}`)).json();
  expect(saved.source_warnings.length).toBeGreaterThan(0);
  await expect(page.locator('.proposal-paper')).toContainText('合成');
  await page.screenshot({path:`${root}/ui/screenshots/synthetic-fallback.png`});
});

test('budget five returns an actual gap of three and no saved result',async({page})=>{
  const item=evidence('deepseek-low-budget');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('未完成');
  await expect(page.locator('.budget-numbers>strong')).toHaveText('8');
  await expect(page.locator('.budget-numbers')).toContainText('/ 5');
  await expect(page.locator('.proposal-paper')).toHaveCount(0);
  await expect(page.locator('.failure-summary')).toContainText(/缺口.*3/);
});
