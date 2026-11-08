import { test, expect } from '@playwright/test';
import fs from 'node:fs';

test('actual PostgreSQL and MCP result renders and survives reload', async ({ page, request, context }) => {
  test.skip(process.env.LIVE_E2E !== '1', 'Run real-model acceptance first, then set LIVE_E2E=1');
  test.setTimeout(90_000);
  const output = JSON.parse(fs.readFileSync('examples/live/02-complete-replan-idempotence/output.json', 'utf8'));
  const sessionId = output.workspace.session.session_id;
  const response = await request.get(`/api/sessions/${sessionId}`);
  expect(response.ok()).toBeTruthy();
  const actual = await response.json();
  expect(actual.mode).toBe('live');
  expect(actual.active_run.status).toBe('completed');
  const errors: string[] = [];
  const consoleIssues: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', entry => { if (['error', 'warning'].includes(entry.type())) consoleIssues.push(entry.text()); });
  await page.goto(`/sessions/${sessionId}`);
  await expect(page.getByText('演示数据', { exact: true })).toHaveCount(0);
  await expect(page.locator('.budget-numbers>strong')).toHaveText('8');
  await expect(page.locator('.proposal-paper')).toContainText('素材与来源');
  await expect(page.locator('.plan-step.step-success')).toHaveCount(5);
  await expect(page.getByTestId('tool-card').first()).toBeVisible();
  await page.screenshot({ path: 'examples/frontend-ui/desktop-live-completed.png', fullPage: true });
  const savedResponse = await request.get(`/api/proposals/${actual.proposals[0].proposal_id}`);
  const saved = await savedResponse.json();
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: '复制方案', exact: true }).click();
  await expect(page.getByText('已复制方案', { exact: true })).toBeVisible();
  // Windows clipboard normalizes line breaks to CRLF; compare the text content.
  await expect.poll(async () => (await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, '\n')).toBe(saved.markdown.replace(/\r\n/g, '\n'));
  await page.getByRole('button', { name: '放大阅读', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText(saved.title);
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'JSON', exact: true }).click();
  const jsonDownload = await download;
  expect(jsonDownload.suggestedFilename()).toMatch(/\.json$/);
  expect(JSON.parse(fs.readFileSync((await jsonDownload.path())!, 'utf8'))).toEqual(saved);
  await jsonDownload.saveAs('examples/ui-label-audit/after/export.json');
  const markdown = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载 Markdown', exact: true }).click();
  const mdDownload = await markdown;
  expect(fs.readFileSync((await mdDownload.path())!, 'utf8')).toBe(saved.markdown);
  await mdDownload.saveAs('examples/ui-label-audit/after/export.md');
  fs.writeFileSync('examples/ui-label-audit/after/copy-reader-inspection.json', JSON.stringify({
    clipboard_matches_saved_after_line_ending_normalization: true, reader_title: saved.title,
    reader_close_label: '关闭', json_export_matches_saved: true, markdown_export_matches_saved: true,
    browser_errors: errors, console: consoleIssues,
  }, null, 2));
  await page.reload();
  await expect(page.locator('.proposal-paper')).toContainText(actual.proposals[0].title);
  expect(errors).toEqual([]);
  expect(consoleIssues).toEqual([]);
});
