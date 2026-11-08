import { defineConfig } from '@playwright/test';
import base from './playwright.config';
const outputRoot=process.env.BOUNDARY_UI_STAGE
  ? `examples/backend-boundaries/${process.env.BOUNDARY_UI_STAGE}/ui`
  : 'examples/backend-boundaries/ui';
export default defineConfig(base, {
  testDir: './tests/backend-ui', timeout: 90_000,
  reporter: [['list'], ['json', { outputFile: `${outputRoot}/report.json` }]],
  outputDir: `${outputRoot}/artifacts`,
  use: { ...base.use, trace: 'on' },
});
