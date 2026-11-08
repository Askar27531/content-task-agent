import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testDir: './tests/external-ui', timeout: 90_000,
  reporter: [['list'], ['json', { outputFile: 'examples/external-live/after/ui/report.json' }]],
  outputDir: 'examples/external-live/after/ui/artifacts',
  use: { ...base.use, trace: 'on' },
});
