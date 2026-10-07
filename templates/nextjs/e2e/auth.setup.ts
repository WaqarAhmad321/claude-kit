import { expect, test as setup } from "@playwright/test";
import { roles, storageStatePath } from "./roles";

// Each role signs in once through the real login form; its cookies are saved for the specs that use the role.
for (const role of roles()) {
  setup(`sign in as ${role.name}`, async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(role.email);
    await page.getByLabel("Password").fill(role.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("navigation")).toBeVisible();
    await page.context().storageState({ path: storageStatePath(role.name) });
  });
}
