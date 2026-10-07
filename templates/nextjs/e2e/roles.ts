export type RoleName = "user" | "admin";

type Role = { name: RoleName } & Record<"email" | "password", string>;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is empty. Set it in .env to a test-stack login; .env.example lists it.`);
  }
  return value;
}

export function roles(): Role[] {
  return [
    { name: "user", email: required("E2E_USER_EMAIL"), password: required("E2E_USER_PASSWORD") },
    { name: "admin", email: required("E2E_ADMIN_EMAIL"), password: required("E2E_ADMIN_PASSWORD") },
  ];
}

export function storageStatePath(name: RoleName): string {
  return `playwright/.auth/${name}.json`;
}
