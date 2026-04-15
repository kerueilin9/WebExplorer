import { test, expect } from "@playwright/test";

test("{{ scenario_name }}", async ({ page }) => {
  await page.goto("{{ start_url }}");

  // TODO: Replay navigation steps discovered by the ADK agent.
  // TODO: Replace placeholder assertions with route-specific checks.

  await expect(page).toHaveURL(/{{ expected_url_pattern }}/);
});
