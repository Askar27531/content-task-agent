import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig(base, {
  testDir: './tests/feature-boundary-ui', timeout: 90_000,
  reporter: [['list'], ['json', { outputFile: 'examples/new-feature-boundaries/after/ui/report.json' }]],
  outputDir: 'examples/new-feature-boundaries/after/ui/artifacts',
  use: { ...base.use, trace: 'on' },
});
