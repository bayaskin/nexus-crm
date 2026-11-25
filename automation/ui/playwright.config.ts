import { defineConfig } from '@playwright/test';
import { playwrightArtifactPolicy } from './playwright-artifact-policy.mjs';

const qaMode = process.env.NEXUS_QA_MODE === 'true';
const artifacts = playwrightArtifactPolicy(process.env.NEXUS_QA_MODE);

// Playwright otherwise captures an AI-readable DOM snapshot into
// error-context.md when a test fails. Suppress that snapshot in isolated QA.
if (qaMode) {
  process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1';
}

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  reporter: qaMode ? [['list']] : [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:8080',
    ...artifacts,
  },
});
