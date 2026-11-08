# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: boundaries.spec.ts >> stop and resume preserve successful tools and render the actual save outcome
- Location: tests\backend-ui\boundaries.spec.ts:136:1

# Error details

```
Error: ENOENT: no such file or directory, open 'D:\Agent\Content-task\examples\backend-boundaries\delivery-fix\23-stop-resume\evidence.json'
```

# Test source

```ts
  1   | import { expect, test, type Page, type APIRequestContext } from '@playwright/test';
  2   | import fs from 'node:fs';
  3   | 
  4   | const stage=process.env.BOUNDARY_UI_STAGE;
  5   | const root=stage ? `examples/backend-boundaries/${stage}` : 'examples/backend-boundaries';
  6   | function evidence(name: string) {
> 7   |   if(stage) return JSON.parse(fs.readFileSync(`${root}/${name}/evidence.json`,'utf8'));
      |                                  ^ Error: ENOENT: no such file or directory, open 'D:\Agent\Content-task\examples\backend-boundaries\delivery-fix\23-stop-resume\evidence.json'
  8   |   const final=`${root}/final/${name}/evidence.json`;
  9   |   return JSON.parse(fs.readFileSync(fs.existsSync(final) ? final : `${root}/after/${name}/evidence.json`,'utf8'));
  10  | }
  11  | async function open(page:Page,request:APIRequestContext,name:string) {
  12  |   const item=evidence(name);const response=await request.get(`/api/sessions/${item.session_id}`);
  13  |   expect(response.ok()).toBeTruthy();const actual=await response.json();expect(actual.mode).toBe('live');
  14  |   await page.goto(`/sessions/${item.session_id}`);
  15  |   await expect(page.locator('.studio-app')).toHaveAttribute('data-session-id',item.session_id);
  16  |   return actual;
  17  | }
  18  | test.beforeEach(()=>fs.mkdirSync(`${root}/ui/screenshots`,{recursive:true}));
  19  | 
  20  | test('ambiguous and withdrawn requirements match server issues without showing invalid confirmed budgets',async({page,request})=>{
  21  |   for(const name of ['01-vague','03-date-only','04-range','07-reversed-dates','08-withdraw-brief']) {
  22  |     const actual=await open(page,request,name);
  23  |     await expect(page.locator('.workspace-status')).toHaveText('待确认需求');
  24  |     await expect(page.locator('.requirements-issues li')).toHaveText(actual.requirements_issues);
  25  |     await expect(page.locator('.budget-numbers')).not.toContainText(/NaN|Infinity|钱别花多|10或20/);
  26  |     await expect(page.locator('.proposal-paper')).toHaveCount(0);
  27  |     expect(actual.active_run).toBeNull();
  28  |   }
  29  |   await page.screenshot({path:`${root}/ui/screenshots/withdrawn.png`});
  30  | });
  31  | 
  32  | test('conditional and bypass messages stay at preview and do not create tools or saved outcomes',async({page,request})=>{
  33  |   for(const name of ['11-conditional','12-bypass-save']) {
  34  |     const actual=await open(page,request,name);expect(actual.active_run).toBeNull();
  35  |     await expect(page.locator('.workspace-status')).toHaveText('待执行指令');
  36  |     await expect(page.getByTestId('plan-status')).toHaveText('待执行指令');
  37  |     await expect(page.locator('.message-assistant').last()).toContainText(/没有启动任务|没有明确执行授权时不会开始/);
  38  |     await expect(page.locator('.saved-note')).toHaveCount(0);
  39  |   }
  40  |   await page.screenshot({path:`${root}/ui/screenshots/authorization.png`});
  41  | });
  42  | 
  43  | test('unsupported real video publishing and payment are explained without pretending a text plan can fulfill them',async({page,request})=>{
  44  |   const actual=await open(page,request,'13-unsupported');expect(actual.active_run).toBeNull();
  45  |   await expect(page.locator('.requirements-issues')).toContainText('没有真实视频');
  46  |   await expect(page.locator('.requirements-issues')).toContainText('发布工具');
  47  |   await expect(page.locator('.requirements-issues')).toContainText('支付');
  48  |   await expect(page.locator('.workspace-status')).toHaveText('待确认需求');
  49  |   await expect(page.locator('.proposal-paper')).toHaveCount(0);
  50  |   await page.screenshot({path:`${root}/ui/screenshots/capability.png`});
  51  | });
  52  | 
  53  | test('strict real-source requirement shows incomplete and blocked rather than fabricated material success',async({page,request})=>{
  54  |   for(const name of ['14-real-only-implicit','15-real-only-english']) {
  55  |     const actual=await open(page,request,name);expect(actual.requirements.real_sources_required).toBe(true);
  56  |     await expect(page.locator('.workspace-status')).toHaveText('未完成');
  57  |     await expect(page.locator('.brief-foot')).toContainText('仅使用真实素材');
  58  |     await expect(page.locator('.failure-summary')).toContainText('Tavily');
  59  |     await expect(page.locator('.material-card')).toHaveCount(0);
  60  |     await expect(page.locator('.plan-step.step-blocked')).toHaveCount(5);
  61  |     await expect(page.locator('.plan-step.step-blocked .step-status')).toHaveText(Array(5).fill('受阻'));
  62  |     await page.locator('.plan-step').first().locator('summary').click();
  63  |     await expect(page.locator('.blocked-reason').first()).toContainText('Tavily');
  64  |     await expect(page.locator('.message-assistant').last()).toContainText('尚未取得');
  65  |   }
  66  |   await page.screenshot({path:`${root}/ui/screenshots/strict-sources.png`});
  67  | });
  68  | 
  69  | test('zero decimal and scheduling gaps retain successful estimate tools and truthful incomplete status',async({page,request})=>{
  70  |   for(const name of ['16-zero-budget','17-decimal-budget','18-short-schedule']) {
  71  |     const actual=await open(page,request,name);
  72  |     await expect(page.locator('.workspace-status')).toHaveText('未完成');
  73  |     await expect(page.locator('.budget-numbers>strong')).toHaveText(actual.cost_summary.production);
  74  |     await expect(page.locator('.proposal-paper')).toHaveCount(0);
  75  |     const estimates=page.getByTestId('tool-card').filter({has:page.locator('[data-field-key="is_feasible"]')});
  76  |     await expect(estimates.first()).toContainText('不满足预算或截止时间');
  77  |     await expect(estimates.first().locator('.tool-state')).toContainText('已完成');
  78  |     if(name==='17-decimal-budget') await expect(page.locator('[data-field-key="cost_gap"]').locator('..').locator('dd').filter({hasText:/^0\.001$/}).first()).toBeVisible();
  79  |     await page.screenshot({path:`${root}/ui/screenshots/${name}.png`});
  80  |   }
  81  | });
  82  | 
  83  | test('stale approval and in-flight changes map to the new waiting plan without a false save',async({page,request})=>{
  84  |   for(const name of ['21-stale-plan-approval','22-change-during-tool']) {
  85  |     const actual=await open(page,request,name);expect(actual.requirements.budget).toBe('8');
  86  |     await expect(page.locator('.workspace-status')).toHaveText('待执行指令');
  87  |     await expect(page.getByTestId('plan-status')).toHaveText('待执行指令');
  88  |     await expect(page.locator('.proposal-paper')).toHaveCount(0);
  89  |     expect(actual.proposals).toEqual([]);
  90  |   }
  91  |   await page.screenshot({path:`${root}/ui/screenshots/change-waits.png`});
  92  | });
  93  | 
  94  | test('three generated text scripts and their actual delivery outcome agree with the backend',async({page,request})=>{
  95  |   const actual=await open(page,request,'20-video-script-supported');
  96  |   // This verifies UI mapping. Strict backend scenario assertions independently
  97  |   // require completion, so rendering an incomplete run cannot mask a regression.
  98  |   expect(['completed','incomplete']).toContain(actual.active_run.status);
  99  |   const generated=page.getByTestId('tool-card').filter({has:page.locator('.tool-card-top strong').filter({hasText:'生成内容脚本'})});
  100 |   await expect(generated).toContainText(/脚本\s*1/);
  101 |   await expect(generated).toContainText(/脚本\s*2/);
  102 |   await expect(generated).toContainText(/脚本\s*3/);
  103 |   await expect(generated.locator('.tool-state')).toContainText('已完成');
  104 |   if(actual.active_run.status==='completed') {
  105 |     await expect(page.locator('.workspace-status')).toHaveText('已完成');
  106 |     await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  107 |     await expect(page.locator('.plan-step.step-blocked')).toHaveCount(0);
```