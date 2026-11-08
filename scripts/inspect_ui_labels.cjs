const { chromium } = require('playwright');
const fs = require('node:fs');

(async () => {
  const folder = 'examples/ui-label-audit/' + (process.env.AUDIT_STAGE || 'before');
  fs.mkdirSync(folder, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const id = group => JSON.parse(fs.readFileSync(`examples/live/${group}/output.json`, 'utf8')).workspace.session.session_id;
    await page.goto('http://127.0.0.1:5173/sessions/' + id('02-complete-replan-idempotence'));
    await page.locator('.proposal-paper').waitFor();
    await page.getByText('查看工具与模型开销', { exact: true }).click();
    const result = { completed: { url: page.url(), header: await page.locator('.chat-heading').innerText(), costs: await page.locator('.costs-section').innerText(), steps: await page.locator('.steps').innerText() } };
    await page.screenshot({ path: `${folder}/desktop.png` });
    const versions = page.getByRole('combobox', { name: '计划版本' });
    await versions.click(); await page.locator('.ant-select-item-option-content').filter({ hasText: 'v1 ·' }).click();
    await page.locator('.historical-notice').waitFor();
    await page.getByRole('button', { name: '概览', exact: true }).click();
    result.historical = { costs: await page.locator('.costs-section').innerText(), overview: await page.locator('.overview-section').innerText(), proposal: await page.locator('.proposal-version').innerText() };
    await page.screenshot({ path: `${folder}/historical.png` });
    await page.goto('http://127.0.0.1:5173/sessions/' + id('04-no-feasible-budget'));
    await page.locator('.failure-summary').waitFor();
    result.incomplete = { header: await page.locator('.chat-heading').innerText(), failure: await page.locator('.failure-summary').innerText() };
    result.visibleFields = await page.locator('.result-object dt').allTextContents();
    await page.screenshot({ path: `${folder}/incomplete.png` });
    await page.goto('http://127.0.0.1:5173/sessions/' + id('02-complete-replan-idempotence'));
    await page.locator('.proposal-paper').waitFor();
    for (const width of [1440, 1280, 1024, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      if (width < 1280) await page.locator('.mobile-switch').getByRole('button', { name: '方案', exact: true }).click();
      await page.screenshot({ path: `${folder}/width-${width}.png` });
      result['layout' + width] = await page.evaluate(() => ({ window: innerWidth, document: document.documentElement.scrollWidth, overflows: [...document.querySelectorAll('.section-title-row,.tool-card-top,.step-status,.brief-grid,.proposal-version,.app-header,.document-nav')].filter(e => e.getBoundingClientRect().width && e.scrollWidth > e.clientWidth + 2).map(e => ({ class: e.className, text: e.innerText, client: e.clientWidth, scroll: e.scrollWidth })) }));
    }
    result.errors = errors;
    fs.writeFileSync(`${folder}/inspection.json`, JSON.stringify(result, null, 2));
    if (errors.length || Object.entries(result).some(([key, value]) => key.startsWith('layout') && (value.document > value.window || value.overflows.length))) throw Error('Browser errors or label overflow found');
    console.log(JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
