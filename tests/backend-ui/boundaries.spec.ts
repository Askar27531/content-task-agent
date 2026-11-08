import { expect, test, type Page, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';

const stage=process.env.BOUNDARY_UI_STAGE;
const root=stage ? `examples/backend-boundaries/${stage}` : 'examples/backend-boundaries';
function evidence(name: string) {
  if(stage) return JSON.parse(fs.readFileSync(`${root}/${name}/evidence.json`,'utf8'));
  const final=`${root}/final/${name}/evidence.json`;
  return JSON.parse(fs.readFileSync(fs.existsSync(final) ? final : `${root}/after/${name}/evidence.json`,'utf8'));
}
async function open(page:Page,request:APIRequestContext,name:string) {
  const item=evidence(name);const response=await request.get(`/api/sessions/${item.session_id}`);
  expect(response.ok()).toBeTruthy();const actual=await response.json();expect(actual.mode).toBe('live');
  await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.studio-app')).toHaveAttribute('data-session-id',item.session_id);
  return actual;
}
test.beforeEach(()=>fs.mkdirSync(`${root}/ui/screenshots`,{recursive:true}));

test('ambiguous and withdrawn requirements match server issues without showing invalid confirmed budgets',async({page,request})=>{
  for(const name of ['01-vague','03-date-only','04-range','07-reversed-dates','08-withdraw-brief']) {
    const actual=await open(page,request,name);
    await expect(page.locator('.workspace-status')).toHaveText('待确认需求');
    await expect(page.locator('.requirements-issues li')).toHaveText(actual.requirements_issues);
    await expect(page.locator('.budget-numbers')).not.toContainText(/NaN|Infinity|钱别花多|10或20/);
    await expect(page.locator('.proposal-paper')).toHaveCount(0);
    expect(actual.active_run).toBeNull();
  }
  await page.screenshot({path:`${root}/ui/screenshots/withdrawn.png`});
});

test('conditional and bypass messages stay at preview and do not create tools or saved outcomes',async({page,request})=>{
  for(const name of ['11-conditional','12-bypass-save']) {
    const actual=await open(page,request,name);expect(actual.active_run).toBeNull();
    await expect(page.locator('.workspace-status')).toHaveText('待执行指令');
    await expect(page.getByTestId('plan-status')).toHaveText('待执行指令');
    await expect(page.locator('.message-assistant').last()).toContainText(/没有启动任务|没有明确执行授权时不会开始/);
    await expect(page.locator('.saved-note')).toHaveCount(0);
  }
  await page.screenshot({path:`${root}/ui/screenshots/authorization.png`});
});

test('unsupported real video publishing and payment are explained without pretending a text plan can fulfill them',async({page,request})=>{
  const actual=await open(page,request,'13-unsupported');expect(actual.active_run).toBeNull();
  await expect(page.locator('.requirements-issues')).toContainText('没有真实视频');
  await expect(page.locator('.requirements-issues')).toContainText('发布工具');
  await expect(page.locator('.requirements-issues')).toContainText('支付');
  await expect(page.locator('.workspace-status')).toHaveText('待确认需求');
  await expect(page.locator('.proposal-paper')).toHaveCount(0);
  await page.screenshot({path:`${root}/ui/screenshots/capability.png`});
});

test('strict real-source requirement shows incomplete and blocked rather than fabricated material success',async({page,request})=>{
  for(const name of ['14-real-only-implicit','15-real-only-english']) {
    const actual=await open(page,request,name);expect(actual.requirements.real_sources_required).toBe(true);
    await expect(page.locator('.workspace-status')).toHaveText('未完成');
    await expect(page.locator('.brief-foot')).toContainText('仅使用真实素材');
    await expect(page.locator('.failure-summary')).toContainText('Tavily');
    await expect(page.locator('.material-card')).toHaveCount(0);
    await expect(page.locator('.plan-step.step-blocked')).toHaveCount(5);
    await expect(page.locator('.plan-step.step-blocked .step-status')).toHaveText(Array(5).fill('受阻'));
    await page.locator('.plan-step').first().locator('summary').click();
    await expect(page.locator('.blocked-reason').first()).toContainText('Tavily');
    await expect(page.locator('.message-assistant').last()).toContainText('尚未取得');
  }
  await page.screenshot({path:`${root}/ui/screenshots/strict-sources.png`});
});

test('zero decimal and scheduling gaps retain successful estimate tools and truthful incomplete status',async({page,request})=>{
  for(const name of ['16-zero-budget','17-decimal-budget','18-short-schedule']) {
    const actual=await open(page,request,name);
    await expect(page.locator('.workspace-status')).toHaveText('未完成');
    await expect(page.locator('.budget-numbers>strong')).toHaveText(actual.cost_summary.production);
    await expect(page.locator('.proposal-paper')).toHaveCount(0);
    const estimates=page.getByTestId('tool-card').filter({has:page.locator('[data-field-key="is_feasible"]')});
    await expect(estimates.first()).toContainText('不满足预算或截止时间');
    await expect(estimates.first().locator('.tool-state')).toContainText('已完成');
    if(name==='17-decimal-budget') await expect(page.locator('[data-field-key="cost_gap"]').locator('..').locator('dd').filter({hasText:/^0\.001$/}).first()).toBeVisible();
    await page.screenshot({path:`${root}/ui/screenshots/${name}.png`});
  }
});

test('stale approval and in-flight changes map to the new waiting plan without a false save',async({page,request})=>{
  for(const name of ['21-stale-plan-approval','22-change-during-tool']) {
    const actual=await open(page,request,name);expect(actual.requirements.budget).toBe('8');
    await expect(page.locator('.workspace-status')).toHaveText('待执行指令');
    await expect(page.getByTestId('plan-status')).toHaveText('待执行指令');
    await expect(page.locator('.proposal-paper')).toHaveCount(0);
    expect(actual.proposals).toEqual([]);
  }
  await page.screenshot({path:`${root}/ui/screenshots/change-waits.png`});
});

test('three generated text scripts and their actual delivery outcome agree with the backend',async({page,request})=>{
  const actual=await open(page,request,'20-video-script-supported');
  // This verifies UI mapping. Strict backend scenario assertions independently
  // require completion, so rendering an incomplete run cannot mask a regression.
  expect(['completed','incomplete']).toContain(actual.active_run.status);
  const generated=page.getByTestId('tool-card').filter({has:page.locator('.tool-card-top strong').filter({hasText:'生成内容脚本'})});
  await expect(generated).toContainText(/脚本\s*1/);
  await expect(generated).toContainText(/脚本\s*2/);
  await expect(generated).toContainText(/脚本\s*3/);
  await expect(generated.locator('.tool-state')).toContainText('已完成');
  if(actual.active_run.status==='completed') {
    await expect(page.locator('.workspace-status')).toHaveText('已完成');
    await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
    await expect(page.locator('.plan-step.step-blocked')).toHaveCount(0);
    await expect(page.locator('.proposal-paper')).toContainText(/脚本\s*1/);
    await expect(page.locator('.proposal-paper')).toContainText(/脚本\s*2/);
    await expect(page.locator('.proposal-paper')).toContainText(/脚本\s*3/);
  } else {
    await expect(page.locator('.workspace-status')).toHaveText('未完成');
    await expect(page.locator('.failure-summary')).toContainText(actual.active_run.reason);
    await expect(page.locator('.plan-step.step-blocked')).toHaveCount(2);
    await expect(page.locator('.plan-step.step-blocked .step-status')).toHaveText(['受阻','受阻']);
    await expect(page.locator('.proposal-paper')).toHaveCount(0);
  }
  await expect(page.locator('[data-untranslated="true"]')).toHaveCount(0);
  await page.getByRole('button',{name:'计划',exact:true}).click();
  await page.screenshot({path:`${root}/ui/screenshots/three-scripts.png`});
});

test('oversize input remains in the composer and actual API validation errors have usable Chinese messages',async({page,request})=>{
  const data=evidence('01-vague');await page.goto(`/sessions/${data.session_id}`);
  const text='字'.repeat(20001);let submitted=0;
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/messages'))submitted++;});
  await page.getByRole('textbox',{name:'输入活动需求'}).fill(text);
  await page.getByRole('button',{name:'发送消息',exact:true}).click();
  await expect(page.getByText('消息内容不能超过20000个字符，请精简后再发送。',{exact:true})).toBeVisible();
  await expect(page.getByRole('textbox',{name:'输入活动需求'})).toHaveValue(text);expect(submitted).toBe(0);
  const response=await request.post(`/api/sessions/${data.session_id}/messages`,{data:{client_message_id:'bad-view-ui',content:'开始',view_context:{plan_version_id:{wrong:'type'}}}});
  expect(response.status()).toBe(422);const body=await response.json();expect(body.error.message).toContain('计划版本编号必须为字符串');expect(JSON.stringify(body)).not.toContain('[object Object]');
  fs.writeFileSync(`${root}/ui/validation.json`,JSON.stringify(body,null,2));
});

test('stop and resume preserve successful tools and render the actual save outcome',async({page,request})=>{
  const actual=await open(page,request,'23-stop-resume');expect(['completed','incomplete']).toContain(actual.active_run.status);
  if(actual.active_run.status==='completed') {
    await expect(page.locator('.workspace-status')).toHaveText('已完成');
    await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
    await expect(page.locator('.plan-step.step-blocked')).toHaveCount(0);
    await expect(page.locator('.proposal-paper')).toBeVisible();
  } else {
    await expect(page.locator('.workspace-status')).toHaveText('未完成');
    await expect(page.locator('.plan-step.step-success')).toHaveCount(4);
    await expect(page.locator('.plan-step.step-blocked')).toHaveCount(1);
    await expect(page.locator('.plan-step.step-blocked .step-status')).toHaveText('受阻');
    await expect(page.locator('.failure-summary')).toContainText(actual.active_run.reason);
    await expect(page.locator('.proposal-paper')).toHaveCount(0);
  }
  await expect(page.locator('.tool-card-top strong').filter({hasText:'检索活动素材'})).toHaveCount(1);
  await expect(page.locator('.tool-card-top strong').filter({hasText:'生成内容脚本'})).toHaveCount(1);
  await page.screenshot({path:`${root}/ui/screenshots/resumed.png`});
});
