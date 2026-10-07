import { expect, test } from "@playwright/test";
import { storageStatePath } from "./roles.ts";

test.describe("signed out", () => {
  test("a protected page sends you to the login page", async ({ page }) => {
    await page.goto("/notes");
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("as a user", () => {
  test.use({ storageState: storageStatePath("user") });

  test("creates a note and finds it in the list", async ({ page }) => {
    const title = `Note ${Date.now()}`;
    await page.goto("/notes");
    await page.getByRole("button", { name: "New note" }).click();
    await page.getByLabel("Title").fill(title);
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("listitem").filter({ hasText: title })).toBeVisible();
  });

  test("a blank title shows the field error", async ({ page }) => {
    await page.goto("/notes");
    await page.getByRole("button", { name: "New note" }).click();
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByLabel("Title")).toHaveAccessibleDescription(/required/i);
  });

  test("the admin area is out of reach", async ({ page }) => {
    await page.goto("/admin/users");
    await expect(page.getByRole("heading", { name: "Not allowed" })).toBeVisible();
  });
});

test.describe("as an admin", () => {
  test.use({ storageState: storageStatePath("admin") });

  test("lists the users", async ({ page }) => {
    await page.goto("/admin/users");
    await expect(page.getByRole("table")).toBeVisible();
  });
});
