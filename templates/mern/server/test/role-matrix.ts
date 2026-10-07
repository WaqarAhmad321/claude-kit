import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { parse } from "yaml";
import { z } from "zod";

const REQUEST_LINE = /^(GET|POST|PUT|PATCH|DELETE) (\/\S*)$/;
const PLACEHOLDER = /\{(\w+)\.(\w+)\}/g;
const FIRST_SERVER_ERROR = 500;
const USAGE_ERROR = 2;

const requestLine = z.string().regex(REQUEST_LINE, "Write requests as '<METHOD> /path'.");
const jsonObject = z.record(z.string(), z.unknown());

const matrixFile = z.object({
  base_url: z.url(),
  auth: z.object({
    signup: z.string().startsWith("/"),
    login: z.string().startsWith("/"),
    admin: z.object({ email: z.email(), password: z.string().min(1) }),
  }),
  roles: z.record(z.string(), z.enum(["user", "admin"]).nullable()),
  fixtures: z
    .record(
      z.string(),
      z.object({ as: z.string(), request: requestLine, body: jsonObject.optional() }),
    )
    .default({}),
  operations: z
    .array(
      z.object({
        request: requestLine,
        body: jsonObject.optional(),
        content_type: z.string().default("application/json"),
        expect: z.record(z.string(), z.number().int().min(100).max(599)),
      }),
    )
    .min(1),
});

type Matrix = z.infer<typeof matrixFile>;
type Cookie = string | null;
type Fixture = Matrix["fixtures"][string];

interface CellResult {
  ok: boolean;
  line: string;
}

function splitRequest(line: string): { method: string; path: string } {
  const [, method, path] = REQUEST_LINE.exec(line) ?? [];
  if (method === undefined || path === undefined) throw new Error(`Bad request line: ${line}`);
  return { method, path };
}

function send(
  matrix: Matrix,
  line: string,
  cookie: Cookie,
  body?: unknown,
  contentType = "application/json",
): Promise<Response> {
  const { method, path } = splitRequest(line);
  const headers = new Headers();
  if (cookie !== null) headers.set("cookie", cookie);
  if (body !== undefined) headers.set("content-type", contentType);
  return fetch(new URL(path, matrix.base_url), {
    method,
    headers,
    body: body === undefined ? null : JSON.stringify(body),
  });
}

async function logIn(matrix: Matrix, email: string, secret: string): Promise<string> {
  const response = await send(matrix, `POST ${matrix.auth.login}`, null, {
    email,
    password: secret,
  });
  if (!response.ok) throw new Error(`Logging in as ${email} failed with ${response.status}.`);
  return response.headers
    .getSetCookie()
    .map((header) => header.split(";")[0])
    .join("; ");
}

async function signUpAndLogIn(matrix: Matrix): Promise<string> {
  const email = `matrix-${randomUUID()}@example.test`;
  const secret = `pw-${randomUUID()}`;
  const response = await send(matrix, `POST ${matrix.auth.signup}`, null, {
    email,
    password: secret,
    display_name: "Role matrix",
  });
  if (response.status !== 201)
    throw new Error(`Signing up ${email} failed with ${response.status}.`);
  return logIn(matrix, email, secret);
}

async function cookiesByRole(matrix: Matrix): Promise<Map<string, Cookie>> {
  const { admin } = matrix.auth;
  const entries = await Promise.all(
    Object.entries(matrix.roles).map(async ([role, kind]) => {
      if (kind === null) return [role, null] as const;
      const cookie =
        kind === "admin"
          ? await logIn(matrix, admin.email, admin.password)
          : await signUpAndLogIn(matrix);
      return [role, cookie] as const;
    }),
  );
  return new Map(entries);
}

function checkMatrix(matrix: Matrix): void {
  const roles = Object.keys(matrix.roles).toSorted();
  for (const [name, fixture] of Object.entries(matrix.fixtures)) {
    if (!matrix.roles[fixture.as])
      throw new Error(`Fixture '${name}' must be created by a signed-in role.`);
  }
  for (const operation of matrix.operations) {
    const expected = Object.keys(operation.expect).toSorted();
    if (expected.join() !== roles.join()) {
      throw new Error(
        `'${operation.request}' must expect a status for exactly these roles: ${roles.join(", ")}.`,
      );
    }
  }
}

async function createFixture(matrix: Matrix, fixture: Fixture, cookies: Map<string, Cookie>) {
  const response = await send(
    matrix,
    fixture.request,
    cookies.get(fixture.as) ?? null,
    fixture.body,
  );
  if (!response.ok)
    throw new Error(`Creating a fixture with '${fixture.request}' failed with ${response.status}.`);
  return jsonObject.parse(await response.json());
}

async function resolvePlaceholders(
  matrix: Matrix,
  line: string,
  cookies: Map<string, Cookie>,
): Promise<string> {
  const names = new Set([...line.matchAll(PLACEHOLDER)].map((match) => match[1] ?? ""));
  const created = new Map(
    await Promise.all(
      [...names].map(async (name) => {
        const fixture = matrix.fixtures[name];
        if (!fixture) throw new Error(`'${line}' uses an unknown fixture '${name}'.`);
        return [name, await createFixture(matrix, fixture, cookies)] as const;
      }),
    ),
  );
  return line.replaceAll(PLACEHOLDER, (_match, name: string, field: string) => {
    const value = created.get(name)?.[field];
    if (typeof value !== "string" && typeof value !== "number") {
      throw new Error(`Fixture '${name}' has no string or number field '${field}'.`);
    }
    return String(value);
  });
}

async function runCell(
  matrix: Matrix,
  operation: Matrix["operations"][number],
  role: string,
  cookies: Map<string, Cookie>,
): Promise<CellResult> {
  const expected = operation.expect[role];
  const line = await resolvePlaceholders(matrix, operation.request, cookies);
  const response = await send(
    matrix,
    line,
    cookies.get(role) ?? null,
    operation.body,
    operation.content_type,
  );
  const ok = response.status === expected && response.status < FIRST_SERVER_ERROR;
  const verdict = ok ? "PASS" : `FAIL expected ${expected ?? "?"}, got`;
  return { ok, line: `${verdict} ${response.status}  ${operation.request}  as ${role}` };
}

const [matrixPath, baseUrl] = process.argv.slice(2);
if (matrixPath === undefined || baseUrl === undefined) {
  process.stderr.write("Usage: node test/role-matrix.ts <path to roles.yaml> <API base URL>\n");
  process.exit(USAGE_ERROR);
}

const matrixSpec = jsonObject.parse(parse(await readFile(matrixPath, "utf8")));
const matrix = matrixFile.parse({ ...matrixSpec, base_url: baseUrl });
checkMatrix(matrix);
const cookies = await cookiesByRole(matrix);
const results = await Promise.all(
  matrix.operations.flatMap((operation) =>
    Object.keys(matrix.roles).map((role) => runCell(matrix, operation, role, cookies)),
  ),
);
const failed = results.filter((result) => !result.ok).length;
process.stdout.write(`${results.map((result) => result.line).join("\n")}\n`);
process.stdout.write(`Role matrix: ${results.length - failed} passed, ${failed} failed.\n`);
process.exitCode = failed === 0 ? 0 : 1;
