import { defineConfig } from '@playwright/test';
import base from './playwright.config';

export default defineConfig(base, {
  testDir: './tests/ui-audit', timeout: 300_000, expect: { timeout: 20_000 },
  reporter: [['list'], ['json', { outputFile: 'examples/ui-label-audit/test-results/report.json' }], ['html', { outputFolder: 'examples/ui-label-audit/test-results/html', open: 'never' }]],
  outputDir: 'examples/ui-label-audit/test-results/artifacts',
  use: { ...base.use, trace: 'on', screenshot: 'only-on-failure' },
});
