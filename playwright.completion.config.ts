import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testDir: './tests/completion-ui', timeout: 90_000,
  reporter: [['list'], ['json', { outputFile: 'examples/backend-completion/ui/report.json' }]],
  outputDir: 'examples/backend-completion/ui/artifacts',
  use: { ...base.use, trace: 'on' },
});
