import { expect, test } from '@playwright/test';
import fs from 'node:fs';

const outputRoot=process.env.BOUNDARY_UI_STAGE
  ? `examples/backend-boundaries/${process.env.BOUNDARY_UI_STAGE}/ui`
  : 'examples/backend-boundaries/ui';

for(const name of ['20-video-script-supported','23-stop-resume']) {
  test(`original failed ${name} resumes without stale blocked reasons or duplicate work`,async({page,request})=>{
    const item=JSON.parse(fs.readFileSync(`examples/backend-boundaries/delivery-fix/recovery/${name}/evidence.json`,'utf8'));
    expect(item.before_resume.active_run.status).toBe('incomplete');
    const response=await request.get(`/api/sessions/${item.session_id}`);expect(response.ok()).toBeTruthy();
    const actual=await response.json();expect(actual.active_run.status).toBe('completed');
    expect(actual.active_run.run_id).toBe(item.before_resume.active_run.run_id);
    await page.goto(`/sessions/${item.session_id}`);
    await expect(page.locator('.studio-app')).toHaveAttribute('data-session-id',item.session_id);
    await expect(page.locator('.workspace-status')).toHaveText('已完成');
    await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
    await expect(page.locator('.plan-step .step-status')).toHaveText(Array(5).fill('已完成'));
    await expect(page.locator('.failure-summary')).toHaveCount(0);
    for(const summary of await page.locator('.plan-step summary').all()) await summary.click();
    await expect(page.locator('.blocked-reason')).toHaveCount(0);
    await expect(page.locator('.tool-card-top strong').filter({hasText:'检索活动素材'})).toHaveCount(1);
    await expect(page.locator('.tool-card-top strong').filter({hasText:'生成内容脚本'})).toHaveCount(1);
    await expect(page.locator('.proposal-paper')).toBeVisible();
    const proposalId=actual.proposals[0].proposal_id;
    const savedResponse=await request.get(`/api/proposals/${proposalId}`);expect(savedResponse.ok()).toBeTruthy();
    const saved=await savedResponse.json();
    expect(saved.production_cost).toBe('8');
    const exported=await request.get(`/api/proposals/${proposalId}/export?format=json`);expect(exported.ok()).toBeTruthy();
    expect(await exported.json()).toEqual(saved);
    const markdown=await request.get(`/api/proposals/${proposalId}/export?format=markdown`);
    expect(await markdown.text()).toBe(saved.markdown);
    if(name.startsWith('20')) for(const i of [1,2,3]) await expect(page.locator('.proposal-paper')).toContainText(new RegExp(`脚本\\s*${i}`));
    fs.mkdirSync(`${outputRoot}/screenshots`,{recursive:true});
    await page.screenshot({path:`${outputRoot}/screenshots/recovered-original-${name.slice(0,2)}.png`});
  });
}
