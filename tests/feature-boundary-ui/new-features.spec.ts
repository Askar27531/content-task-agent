import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
const root='examples/new-feature-boundaries/after';
const evidence=(name:string)=>JSON.parse(fs.readFileSync(`${root}/${name}/evidence.json`,'utf8'));
test.beforeEach(()=>fs.mkdirSync(`${root}/ui/screenshots`,{recursive:true}));

test('mixed malformed metadata stays partial and all visible labels match the actual result',async({page,request})=>{
  const item=evidence('mixed-shapes');const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  const operation=item.tool_calls.find((op:{tool_name:string})=>op.tool_name==='search_web_materials');
  const card=page.locator(`#call-${operation.tool_call_id}`);
  await expect(card.locator('.tool-state')).toHaveText(/部分成功/);
  await expect(card.locator('[data-field-key="available_count"]+dd')).toHaveText('1');
  await expect(card.locator('[data-field-key="missing"]+dd')).toContainText('第1项');
  await expect(page.locator('.material-card')).toHaveCount(1);
  await expect(page.locator('.material-card')).toContainText('协议测试夹具');
  await expect(page.locator('[data-untranslated="true"]')).toHaveCount(0);
  expect(errors).toEqual([]);
  const actual=await (await request.get(`/api/tool-calls/${operation.tool_call_id}`)).json();
  expect(actual.output.materials).toHaveLength(1);expect(typeof actual.output.materials[0].title).toBe('string');
  await card.scrollIntoViewIfNeeded();await page.screenshot({path:`${root}/ui/screenshots/mixed-shapes.png`});
});

test('duplicate sources render two unique cards and saved references agree with API',async({page,request})=>{
  const item=evidence('duplicates');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.material-card')).toHaveCount(2);
  const urls=await page.locator('.material-card a.source-link').evaluateAll(links=>links.map(a=>(a as HTMLAnchorElement).href));
  expect(new Set(urls).size).toBe(2);
  const proposal=await (await request.get(`/api/proposals/${item.state.proposals[0].proposal_id}`)).json();
  expect(new Set(proposal.materials.map((m:{material_id:string})=>m.material_id)).size).toBe(2);
  await expect(page.locator('.proposal-paper')).toContainText(proposal.title);
});

test('strict minimum and partial then failure preserve valid sources without claiming a saved proposal',async({page})=>{
  for(const name of ['strict-minimum','partial-then-failure']) {
    const item=evidence(name);await page.goto(`/sessions/${item.session_id}`);
    await expect(page.locator('.workspace-status')).toHaveText('未完成');
    await expect(page.locator('.material-card')).toHaveCount(1);
    await expect(page.locator('.material-card .source-tag')).toHaveText('真实来源');
    await expect(page.locator('.failure-summary')).toContainText('素材');
    await expect(page.locator('.proposal-paper')).toHaveCount(0);
    if(name==='partial-then-failure') await expect(page.locator('.tool-card.tool-failed')).not.toHaveCount(0);
  }
  await page.screenshot({path:`${root}/ui/screenshots/preserved-partial-failure.png`});
});

test('copy changes regenerate the script and both saved versions remain accessible',async({page,request})=>{
  const item=evidence('changed-copy');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  const proposals=item.state.proposals;
  expect(proposals).toHaveLength(2);
  for(const ref of proposals) {
    const actual=await (await request.get(`/api/proposals/${ref.proposal_id}`)).json();
    expect(actual.version).toBe(ref.version);
    expect(actual.requirements.topic).toBe(item.state.requirements.topic);
  }
  const selects=page.locator('#doc-proposal .ant-select');await expect(selects).toHaveCount(1);
  await selects.click();await page.getByText(new RegExp(`^方案 v${proposals[0].version} · `)).click();
  const original=await (await request.get(`/api/proposals/${proposals[0].proposal_id}`)).json();
  await expect(page.locator('.proposal-paper')).toContainText(original.structured.content.replace(/^#+\s*/,'').split('\n')[0]);
  await page.screenshot({path:`${root}/ui/screenshots/old-copy-version.png`});
});

test('source policy changes show current real materials and retain the original synthetic document',async({page,request})=>{
  const item=evidence('stricter-sources');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.brief-foot')).toContainText('仅使用真实素材');
  await expect(page.locator('.material-card .source-tag')).toHaveText(['真实来源']);
  const old=await (await request.get(`/api/proposals/${item.state.proposals[0].proposal_id}`)).json();
  expect(old.materials.every((m:{source_type:string})=>m.source_type==='synthetic')).toBe(true);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
});

test('exhausted resume stays incomplete and does not display a new execution or save',async({page})=>{
  const item=evidence('exhausted-resume');await page.goto(`/sessions/${item.session_id}`);
  await expect(page.locator('.workspace-status')).toHaveText('未完成');
  await expect(page.locator('.message-assistant').last()).toContainText('不能通过续跑重置');
  await expect(page.locator('.proposal-paper')).toHaveCount(0);
  await expect(page.locator('.material-card')).toHaveCount(0);
  await page.screenshot({path:`${root}/ui/screenshots/exhausted-resume.png`});
});

test('actual active SSE gap triggers resync and shows all fixture messages exactly once',async({page,request})=>{
  const sid=(await (await request.post('/api/sessions')).json()).session_id;
  let streams=0;page.on('request',r=>{if(r.url().includes(`/sessions/${sid}/events?`))streams++;});
  await page.goto(`/sessions/${sid}`);
  await expect(page.locator('.studio-app')).toHaveAttribute('data-session-id',sid);
  await expect.poll(()=>streams).toBe(1);
  const code=`from app.store import add_message,db_factory,EventRow,SessionRow\nimport copy\nsid='${sid}'\nwith db_factory.begin() as db:\n row=db.get(SessionRow,sid,with_for_update=True)\n state=copy.deepcopy(row.state)\n for n in range(3):\n  add_message(state,'实时事件缺口协议夹具 '+str(n))\n  row.revision+=1\n  if n!=1: db.add(EventRow(session_id=sid,seq=row.revision,type='message.completed',payload={'payload':{'fixture':True}}))\n row.state=state`;
  execFileSync('.venv/Scripts/python.exe',['-c',code],{env:{...process.env,PYTHONPATH:'src/backend',PYTHONUTF8:'1'}});
  await expect(page.locator('.message-assistant')).toHaveCount(3);
  await expect.poll(()=>streams).toBeGreaterThanOrEqual(2);
  for(let n=0;n<3;n++)await expect(page.locator('.message-assistant').filter({hasText:`实时事件缺口协议夹具 ${n}`})).toHaveCount(1);
  await expect(page.locator('.connection-banner')).toHaveCount(0);
  fs.writeFileSync(`${root}/ui/sse-gap.json`,JSON.stringify({session_id:sid,actual_api_sse:true,fixture_only:true,stream_connections:streams},null,2));
});
