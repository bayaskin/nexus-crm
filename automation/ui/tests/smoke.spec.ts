
import { test, expect } from '../fixtures/auth';

test('Nexus dashboard loads and API is connected', async ({
  page,
  authenticatedPage,
}) => {
  await page.goto('/');

  await expect(
    page.getByRole('heading', { name: /^Welcome,/ })
  ).toBeVisible();

  await expect(page.getByText('API Connected')).toBeVisible();
});
