import { test, expect, type Page } from '@playwright/test';

async function say(page: Page, content: string) {
  await page.getByRole('textbox', { name: '输入活动需求' }).fill(content);
  await page.getByRole('textbox', { name: '输入活动需求' }).press('Enter');
}
test('empty workspace has no invented results or task control buttons', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/demo');
  await expect(page.getByRole('heading', { name: /下一场好活动，\s*从一个想法开始。/ })).toBeVisible();
  await expect(page.getByText('演示数据', { exact: true })).toBeVisible();
  await expect(page.getByTestId('tool-card')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^(开始执行|继续执行|停止)$/ })).toHaveCount(0);
  await page.screenshot({ path: 'examples/frontend-ui/desktop-empty.png', fullPage: true });
  expect(errors).toEqual([]);
});
test('chat clarification, explicit execution, budget replan, save and restore', async ({ page }) => {
  await page.goto('/demo');
  await say(page, '为春季新品做内容推广');
  await expect(page.locator('.message-assistant').last()).toContainText('内容制作预算');
  await expect(page.locator('.message-assistant').last()).toContainText('截止日期与时间');
  await say(page, '预算 10，2030-12-01 18:00 前完成');
  await expect(page.locator('.plan-step')).toHaveCount(5);
  await say(page, '先别开始，解释一下执行过程');
  await expect(page.locator('.message-assistant').last()).toContainText('不会开始执行');
  await expect(page.getByTestId('tool-card')).toHaveCount(0);
  await say(page, '开始');
  await expect(page.locator('.message-assistant').last()).toContainText('方案已保存');
  await expect(page.locator('.budget-numbers>strong')).toHaveText('8');
  await expect(page.locator('.change-summary')).toContainText('精简版');
  await expect(page.locator('.proposal-paper')).toContainText('制作预算');
  await expect(page.getByTestId('tool-card')).toHaveCount(6);
  await expect.poll(() => page.locator('.chat-scroll').evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThan(80);
  await page.screenshot({ path: 'examples/frontend-ui/desktop-completed.png', fullPage: true });
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'JSON', exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.json$/);
  await page.reload();
  await expect(page.locator('.proposal-paper')).toContainText('制作预算');
});
test('injected failure preserves material result and resumes in chat', async ({ page }) => {
  await page.goto('/demo');
  await say(page, '为新品推广演示一次工具失败，预算 10，2030-12-01 18:00 前完成');
  await expect(page.locator('.plan-step')).toHaveCount(5);
  await say(page, '开始');
  await expect(page.locator('.message-assistant').last()).toContainText('演示故障');
  await expect(page.locator('.tool-failed')).toHaveCount(1);
  await say(page, '从失败处继续');
  await expect(page.locator('.message-assistant').last()).toContainText('方案已保存');
  await expect(page.locator('.tool-card-top strong').filter({ hasText: '检索活动素材' })).toHaveCount(1);
  await expect(page.locator('.tool-failed')).toHaveCount(1);
});
test('narrow window switches workspace and opens simple history drawer', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto('/demo');
  await expect(page.getByRole('textbox', { name: '输入活动需求' })).toBeVisible();
  await page.locator('.mobile-switch').getByRole('button', { name: '方案', exact: true }).click();
  await expect(page.getByRole('heading', { name: '活动概览' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '输入活动需求' })).not.toBeVisible();
  await page.screenshot({ path: 'examples/frontend-ui/narrow-document.png', fullPage: true });
  await page.getByRole('button', { name: '打开历史会话' }).click();
  await expect(page.locator('.history-drawer')).toBeVisible();
  await expect(page.locator('.history-drawer').getByRole('button', { name: '新建会话' })).toBeVisible();
});
