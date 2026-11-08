import { expect, test } from '@playwright/test';
import fs from 'node:fs';

const root='examples/backend-completion';
const evidence=(name:string)=>JSON.parse(fs.readFileSync(`${root}/${name}/evidence.json`,'utf8'));
test.beforeEach(()=>fs.mkdirSync(`${root}/ui/screenshots`,{recursive:true}));

test('actual reused operations link to current steps and do not appear as preparation',async({page,request})=>{
  const item=evidence('cross-run-reuse');
  const response=await request.get(`/api/sessions/${item.session_id}`);const actual=await response.json();
  expect(actual.active_run.status).toBe('completed');
  await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  const operations=item.tool_calls.filter((op:{tool_name:string})=>['search_synthetic_materials','generate_script'].includes(op.tool_name));
  expect(operations).toHaveLength(2);
  for(const operation of operations) await expect(page.locator(`#call-${operation.tool_call_id} .tool-card-foot`)).toContainText('复用历史结果');
  const reused=page.locator(`#call-${operations[0].tool_call_id}`);
  await reused.getByRole('button',{name:'查看对应步骤'}).click();
  await expect(page.locator('.plan-step').first()).toBeInViewport();
  await expect(page.locator('.proposal-paper')).toBeVisible();
  await page.screenshot({path:`${root}/ui/screenshots/reused.png`});
});

test('partial search and strict empty results match actual server status and source labels',async({page,request})=>{
  const partial=evidence('search-partial');
  await page.goto(`/sessions/${partial.session_id}`);
  await expect(page.locator('.tool-card.tool-partial_success .tool-state')).toContainText('部分成功');
  await expect(page.locator('.material-card').first()).toContainText('协议测试夹具');
  await expect(page.locator('[data-untranslated="true"]')).toHaveCount(0);
  await page.screenshot({path:`${root}/ui/screenshots/partial.png`});
  const strict=evidence('strict-real-empty');
  const actual=await (await request.get(`/api/sessions/${strict.session_id}`)).json();
  expect(actual.active_run.status).toBe('incomplete');
  await page.goto(`/sessions/${strict.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('未完成');
  await expect(page.locator('.proposal-paper')).toHaveCount(0);
  await expect(page.locator('.failure-summary')).toBeVisible();
  await page.screenshot({path:`${root}/ui/screenshots/strict-empty.png`});
});

test('offline browser receives one real accepted message after reconnect without resubmission',async({page,request,context})=>{
  const session=await (await request.post('/api/sessions')).json();const sid=session.session_id;
  await page.goto(`/sessions/${sid}`);
  await expect(page.locator('.studio-app')).toHaveAttribute('data-session-id',sid);
  await context.setOffline(true);
  const marker=`断线恢复检查 ${Date.now()}：这只是状态询问，不要执行，现在还缺什么信息？`;
  const body={content:marker,client_message_id:crypto.randomUUID()};
  const receipt=await request.post(`/api/sessions/${sid}/messages`,{data:body});expect(receipt.status()).toBe(202);
  await context.setOffline(false);
  await expect(page.locator('.message-user').filter({hasText:marker})).toHaveCount(1);
  await expect(page.locator('.message-assistant')).toHaveCount(1,{timeout:60_000});
  await context.setOffline(true);await context.setOffline(false);
  await expect(page.locator('.message-user').filter({hasText:marker})).toHaveCount(1);
  await expect(page.locator('.connection-banner')).toHaveCount(0);
  const duplicate=await request.post(`/api/sessions/${sid}/messages`,{data:body});expect(duplicate.status()).toBe(200);
  const accepted=await duplicate.json();expect(accepted.duplicate).toBe(true);
  const snapshot=await (await request.get(`/api/sessions/${sid}`)).json();expect(snapshot.active_run).toBeNull();
  expect(snapshot.proposals).toHaveLength(0);
  fs.writeFileSync(`${root}/ui/offline-evidence.json`,JSON.stringify({session_id:sid,content:marker,receipt:await receipt.json(),duplicate:accepted,workspace:snapshot},null,2));
  await page.screenshot({path:`${root}/ui/screenshots/offline-restored.png`});
});

test('resync retries after a snapshot HTTP failure (transport fault injection only)',async({page})=>{
  const item=evidence('transport');let snapshots=0;let streams=0;
  await page.route(`**/api/sessions/${item.session_id}`,async route=>{
    snapshots++;
    if(snapshots===2) await route.fulfill({status:503,json:{error:{message:'浏览器传输测试：仅一次快照失败'}}});
    else await route.continue();
  });
  await page.route(`**/api/sessions/${item.session_id}/events?*`,async route=>{
    streams++;
    if(streams<=2) await route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'resync_required',session_seq:3,session_id:item.session_id,payload:{}})+'\n\n'});
    else await route.continue();
  });
  await page.goto(`/sessions/${item.session_id}`);
  await expect.poll(()=>snapshots).toBeGreaterThanOrEqual(3);
  await expect.poll(()=>streams).toBeGreaterThanOrEqual(3);
  await expect(page.locator('.message-assistant')).toHaveCount(3);
  await expect(page.locator('.connection-banner')).toHaveCount(0);
  await page.screenshot({path:`${root}/ui/screenshots/resync-restored.png`});
});
