import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

// One text page and one scanned page; the API's tests use the same file.
// (Playwright loads specs as CommonJS here, so __dirname rather than import.meta.)
const PDF = resolve(
  __dirname,
  "../../../services/api/tests/fixtures/two-pages.pdf",
);

test("upload a PDF, ask about it, and see the source passage highlighted on its page", async ({
  page,
}) => {
  // Wait for hydration: a file picked before React attaches its handler is lost.
  await page.goto("/documents", { waitUntil: "networkidle" });
  await page.locator('input[type="file"]').setInputFiles(PDF);
  // New, or already uploaded (CI's API check uploads it first): stored once either way.
  await expect(page.getByRole("list", { name: "Uploads" })).toContainText(
    /queued for reading|Already in the collection/,
  );
  const row = page
    .getByRole("region", { name: "Document list" })
    .getByRole("listitem")
    .filter({ hasText: "two-pages" });
  await expect(row).toHaveAttribute("data-status", "ready", {
    timeout: 200_000,
  });

  await row.getByRole("link", { name: "Ask about two-pages" }).click();
  await expect(page).toHaveURL(/\?document=/);
  await expect(page.getByRole("button", { name: /two-pages/ })).toBeVisible(); // filter set

  await page.getByLabel("Your question").fill("Which risks were identified?");
  await page.getByLabel("Your question").press("Enter");
  await expect(page.locator('article[aria-busy="true"]')).toHaveCount(0, {
    timeout: 120_000,
  });

  // The real model cites a passage. The fake one (CI) cites nothing, so the
  // answer is refused and offers the closest passages. Either way, one opens.
  const citations = page.getByRole("button", { name: /^Show source \d+$/ });
  if (await citations.count()) {
    await citations.first().click();
  } else {
    await expect(page.getByText("Not found in these documents")).toBeVisible();
    await page
      .getByText("Closest passages")
      .locator("..")
      .getByRole("button")
      .first()
      .click();
  }

  const shown = page.getByTestId("pdf-page");
  await expect(shown.locator("canvas")).toBeVisible();
  await expect(shown.locator("[data-highlight]").first()).toBeVisible();
  const panel = page.getByRole("complementary", { name: "Sources" });
  await expect(panel).toContainText("two-pages");
  await expect(panel).toContainText(/p\. [12]/);
});
