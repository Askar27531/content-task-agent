import { expect, test, type Page, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';

const root = 'examples/ui-label-audit';
const baseApi = '/api';
const exampleId = (name: string): string => JSON.parse(fs.readFileSync(`examples/live/${name}/output.json`, 'utf8')).workspace.session.session_id;
const completeId = exampleId('02-complete-replan-idempotence');
const incompleteId = exampleId('04-no-feasible-budget');
const resumedId = exampleId('03-tool-failure-resume');
const consoleIssues = new WeakMap<Page, string[]>();
async function snapshot(request: APIRequestContext, id: string) {
  const response = await request.get(`${baseApi}/sessions/${id}`);
  expect(response.ok()).toBeTruthy();
  const value = await response.json(); expect(value.mode).toBe('live'); return value;
}
async function open(page: Page, id: string) {
  await page.goto(`/sessions/${id}`); await expect(page.locator('.brief-topic')).not.toContainText('等待你的第一个想法');
}
async function noUntranslatedFields(page: Page) {
  await expect(page.locator('[data-untranslated="true"]')).toHaveCount(0);
  await expect(page.locator('.chat-pane')).not.toContainText('[object Object]');
}
async function selectPlan(page: Page, version: number) {
  await page.getByRole('combobox', { name: '计划版本' }).click();
  await page.getByRole('option', { name: new RegExp(`^v${version} ·`) }).click();
}
async function contrastAudit(page: Page) {
  return page.evaluate(() => {
    const rgba = (s: string) => (s.match(/[\d.]+/g) || []).map(Number);
    const luminance = (v: number[]) => v.slice(0, 3).map(n => n / 255).map(n => n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4).reduce((sum, n, i) => sum + n * [0.2126, 0.7152, 0.0722][i], 0);
    return [...document.querySelectorAll<HTMLElement>('.field-label,.tool-state,.step-status>span:last-child,.budget-note,.over-budget-label,.material-top .ant-tag,.plan-state,.cost-list dt,.result-object dt')]
      .filter(e => !!e.getBoundingClientRect().width && getComputedStyle(e).visibility !== 'hidden')
      .map(e => {
        let node: HTMLElement | null = e; let background = [255, 255, 255];
        while (node) { const color = rgba(getComputedStyle(node).backgroundColor); if (color.length === 3 || color[3] === 1) { background = color; break; } node = node.parentElement; }
        const style = getComputedStyle(e); const light = [luminance(rgba(style.color)), luminance(background)].sort((a, b) => b - a);
        return { text: e.textContent?.trim(), ratio: (light[0] + 0.05) / (light[1] + 0.05), fontSize: parseFloat(style.fontSize) };
      });
  });
}

test.beforeEach(async ({ page, request }) => {
  fs.mkdirSync(`${root}/after`, { recursive: true });
  const issues: string[] = []; consoleIssues.set(page, issues);
  page.on('console', entry => { if (['error', 'warning'].includes(entry.type())) issues.push(entry.text()); });
  const health = await request.get('/api/health'); expect(health.ok()).toBeTruthy();
  expect((await health.json()).storage).toBe('postgresql');
});
test.afterEach(async ({ page }, testInfo) => {
  const issues = consoleIssues.get(page) || [];
  await testInfo.attach('browser-console', { body: JSON.stringify(issues, null, 2), contentType: 'application/json' });
  expect(issues).toEqual([]);
});

test('real completed and historical plans have accurate Chinese labels, costs, scope and dates', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const actual = await snapshot(request, completeId);
  await open(page, completeId); await expect(page.locator('.proposal-paper')).toBeVisible();
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  await expect(page.locator('.budget-numbers>strong')).toHaveText(actual.cost_summary.production);
  await expect(page.getByTestId('plan-status')).toHaveText('已完成');
  await expect(page.locator('.step-title small').first()).toHaveText('合成素材检索');
  await page.getByText('查看工具与模型开销', { exact: true }).click();
  await expect(page.locator('.cost-list')).toContainText(`${actual.cost_summary.tool_simulated} 模拟单位`);
  await expect(page.locator('.cost-list')).toContainText('本会话已知模型输入 / 输出');
  await expect(page.locator('.cost-list')).toContainText('未提供计价');
  await expect(page.locator('.result-object dt').first()).toBeVisible();
  await noUntranslatedFields(page);
  await expect(page.locator('.tool-success').filter({ hasText: '等待结果' })).toHaveCount(0);
  await expect(page.locator('.tool-success').filter({ hasText: '无外部链接' }).first()).toBeVisible();
  await expect(page.locator('.tool-success').filter({ hasText: '北京时间' }).first()).toBeVisible();
  await selectPlan(page, 1);
  await expect(page.locator('.historical-notice')).toContainText('历史计划 v1');
  await expect(page.getByTestId('plan-status')).toHaveText('已被替代');
  await expect(page.locator('.budget-numbers>strong')).toHaveText('12');
  await expect(page.locator('.over-budget-label')).toHaveText('超出预算');
  await expect(page.locator('.cost-list')).toContainText('2030/12/01 19:00');
  await expect(page.locator('.saved-note')).toContainText('来自计划 v2');
  await expect(page.locator('.version-scope-notice')).toContainText('当前查看的计划 v1');
  await page.getByRole('button', { name: '概览', exact: true }).click();
  await page.screenshot({ path: `${root}/after/historical-verified.png` });
  await page.getByRole('button', { name: '返回最新', exact: true }).click();
  await expect(page.locator('.budget-numbers>strong')).toHaveText('8');
  await expect(page.locator('.version-scope-notice')).toHaveCount(0);
  const contrast = await contrastAudit(page); fs.writeFileSync(`${root}/after/contrast.json`, JSON.stringify(contrast, null, 2));
  expect(contrast.filter(item => item.ratio < 4.5 || item.fontSize < 11)).toEqual([]);
  expect(errors).toEqual([]);
});

test('real incomplete run does not leak a completed proposal or call an infeasible estimate a failed tool', async ({ page, request }) => {
  await open(page, completeId); await expect(page.locator('.proposal-paper')).toBeVisible();
  const actual = await snapshot(request, incompleteId);
  await open(page, incompleteId);
  await expect(page.locator('.workspace-status')).toHaveText('未完成');
  await expect(page.locator('.failure-summary')).toContainText('预算缺口 3');
  await expect(page.locator('.budget-numbers>strong')).toHaveText(actual.cost_summary.production);
  await expect(page.locator('.over-budget-label')).toHaveText('超出预算');
  await expect(page.locator('.proposal-paper')).toHaveCount(0);
  await expect(page.locator('.saved-note')).toHaveCount(0);
  const estimates = page.getByTestId('tool-card').filter({ has: page.locator('[data-field-key="is_feasible"]') });
  await expect(estimates.first()).toContainText('不满足预算或截止时间');
  await expect(estimates.first().locator('.tool-state')).toContainText('已完成');
  await noUntranslatedFields(page);
  await page.screenshot({ path: `${root}/after/incomplete-verified.png` });
});

test('real resumed run preserves failed attempt labels alongside a completed latest plan', async ({ page }) => {
  await open(page, resumedId);
  await expect(page.locator('.workspace-status')).toHaveText('已完成');
  const failed = page.locator('.tool-failed'); await expect(failed).toHaveCount(1);
  await expect(failed.locator('.tool-state')).toContainText('失败');
  await expect(failed.locator('.tool-card-foot')).not.toContainText('正在处理');
  await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  await noUntranslatedFields(page);
  await failed.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${root}/after/failed-attempt-verified.png` });
});

test('real responsive tags and version options fit desktop, narrow and phone widths', async ({ page }) => {
  await open(page, completeId); await expect(page.locator('.proposal-paper')).toBeVisible();
  const layouts = [];
  for (const width of [1440, 1280, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    if (width < 1280) {
      await page.locator('.mobile-switch').getByRole('button', { name: '方案', exact: true }).click();
      await expect(page.getByRole('textbox', { name: '输入活动需求' })).not.toBeVisible();
    }
    await page.getByRole('button', { name: '计划', exact: true }).click();
    await expect(page.getByTestId('plan-status')).toHaveText('已完成');
    await page.getByRole('combobox', { name: '计划版本' }).click();
    await expect(page.getByRole('option', { name: /^v1/ })).toBeVisible();
    await page.keyboard.press('Escape');
    const layout = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll<HTMLElement>('.section-title-row,.tool-card-top,.step-status,.brief-grid,.proposal-version,.app-header,.document-nav')]
        .filter(e => !!e.getBoundingClientRect().width && e.scrollWidth > e.clientWidth + 2).map(e => ({ selector: e.className, client: e.clientWidth, scroll: e.scrollWidth })) }));
    expect(layout.document).toBeLessThanOrEqual(width); expect(layout.overflow).toEqual([]); layouts.push(layout);
    await page.screenshot({ path: `${root}/after/verified-width-${width}.png` });
    await page.getByRole('button', { name: '素材', exact: true }).click();
    await expect(page.locator('.source-tag').first()).toHaveText('合成素材');
    const contrast = await contrastAudit(page); expect(contrast.filter(item => item.ratio < 4.5 || item.fontSize < 11)).toEqual([]);
  }
  fs.writeFileSync(`${root}/after/layouts.json`, JSON.stringify(layouts, null, 2));
});

test('new real browser conversation clarifies, previews, executes with live labels, replans and saves', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  const submissions: unknown[] = [];
  await page.goto('/');
  await page.getByRole('button', { name: '新建会话', exact: true }).click();
  await page.waitForURL(/\/sessions\//);
  const id = page.url().split('/sessions/')[1];
  async function say(content: string) {
    const accepted = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/sessions/${id}/messages`));
    await page.getByRole('textbox', { name: '输入活动需求' }).fill(content);
    await page.getByRole('textbox', { name: '输入活动需求' }).press('Enter');
    const response = await accepted; expect(response.ok()).toBeTruthy();
    const result = await response.json(); submissions.push({ content, accepted: result });
    return result.message_id as string;
  }
  async function replyAfter(messageId: string) {
    await expect.poll(async () => {
      const response = await request.get(`/api/sessions/${id}/messages?limit=100`); const timeline = (await response.json()).items;
      const user = timeline.find((item: { item_id: string }) => item.item_id === messageId);
      return !!user && timeline.some((item: { item_seq: number; role: string; processing?: boolean }) => item.item_seq > user.item_seq && item.role === 'assistant' && !item.processing);
    }, { timeout: 90_000, intervals: [500, 1000] }).toBeTruthy();
  }
  await replyAfter(await say('请为本地读书交流会策划内容推广活动，先梳理需求。'));
  await expect(page.locator('.message-assistant').last()).toContainText('预算');
  await expect(page.locator('.message-assistant').last()).toContainText(/截止|日期|时间/);
  expect((await snapshot(request, id)).active_run).toBeNull();
  await replyAfter(await say('制作预算10个模拟单位，开始2030-12-01 08:00，截止2030-12-03 18:00，均北京时间。视觉先采用高规格 premium，允许合成素材。请先给计划。'));
  await expect(page.getByTestId('plan-status')).toHaveText('待执行指令');
  await expect(page.locator('.budget-heading')).toContainText('待估算');
  await expect(page.locator('.plan-step')).toHaveCount(5);
  expect((await snapshot(request, id)).active_run).toBeNull();
  await page.screenshot({ path: `${root}/after/new-preview.png` });
  await replyAfter(await say('先别开始，只查看计划。'));
  expect((await snapshot(request, id)).active_run).toBeNull();
  await say('按这份高规格计划开始执行。');
  await expect(page.locator('.workspace-status')).toHaveText(/执行中|排队中/, { timeout: 60_000 });
  const running = page.locator('.tool-running').first(); await expect(running).toBeVisible({ timeout: 90_000 });
  await expect(running.locator('.tool-state')).toContainText('执行中');
  await page.screenshot({ path: `${root}/after/new-running.png` });
  await expect(page.locator('.workspace-status')).toHaveText('已完成', { timeout: 180_000 });
  await expect(page.locator('.budget-numbers>strong')).toHaveText('8');
  await expect(page.getByTestId('plan-status')).toHaveText('已完成');
  await expect(page.locator('.proposal-paper')).toBeVisible();
  await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  await noUntranslatedFields(page);
  const actual = await snapshot(request, id);
  const timeline = await (await request.get(`/api/sessions/${id}/messages?limit=100`)).json();
  const calls = await Promise.all(timeline.items.filter((item: { kind: string }) => item.kind === 'tool').map(async (item: { tool_call_id: string }) => (await request.get(`/api/tool-calls/${item.tool_call_id}`)).json()));
  expect(calls.filter(call => call.tool_name === 'estimate_proposal' && call.status === 'success').map(call => call.output.production_cost)).toEqual(['12', '8']);
  const saved = await (await request.get(`/api/proposals/${actual.proposals[0].proposal_id}`)).json();
  fs.writeFileSync(`${root}/after/new-conversation.json`, JSON.stringify({ actual: true, url: page.url(), submissions, workspace: actual, timeline, tool_calls: calls, proposal: saved, browser_errors: errors }, null, 2));
  await page.screenshot({ path: `${root}/after/new-completed.png` });
  await page.reload(); await expect(page.locator('.proposal-paper')).toContainText(saved.title);
  expect(errors).toEqual([]);
});
