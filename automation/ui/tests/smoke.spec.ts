import { test, expect } from '@playwright/test';

test('Nexus dashboard loads and API is connected', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome to Nexus' })).toBeVisible();
  await expect(page.getByText('API Connected')).toBeVisible();
});
