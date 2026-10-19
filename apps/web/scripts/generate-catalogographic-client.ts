import { execFile } from "node:child_process";
import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../../..");
const inputPath = resolve(repoRoot, "docs/architecture/catalogographic-openapi-v1-m0804.json");
const clientDir = resolve(repoRoot, "apps/web/src/generated");
const typesOutput = resolve(clientDir, "catalogographic-openapi.ts");
const clientOutput = resolve(clientDir, "catalogographic-client.ts");

function formatUnifiedDiff(expected: string, actual: string, label: string): string {
  const expectedLines = expected.split(/\r?\n/);
  const actualLines = actual.split(/\r?\n/);
  const max = Math.max(expectedLines.length, actualLines.length);
  const lines: string[] = [];
  for (let index = 0; index < max; index += 1) {
    const left = expectedLines[index] ?? "";
    const right = actualLines[index] ?? "";
    if (left === right) continue;
    lines.push(`- ${left}`);
    lines.push(`+ ${right}`);
  }
  return `--- ${label}\n+++ ${label}\n${lines.join("\n")}`;
}

export async function buildSchemaClientArtifacts() {
  const cliPath = resolve(repoRoot, "apps/web/node_modules/.bin/openapi-typescript");
  const tempTypesOutput = resolve(repoRoot, "apps/web/src/generated/.catalogographic-openapi.ts.tmp");
  await execFileAsync(process.execPath, [cliPath, inputPath, "--output", tempTypesOutput, "--alphabetize"], {
    cwd: resolve(repoRoot, "apps/web"),
  });
  const typeOutput = await readFile(tempTypesOutput, "utf8");
  await readFile(tempTypesOutput, "utf8");

  const generatedTypes = [
    "// This file is generated from docs/architecture/catalogographic-openapi-v1-m0804.json.",
    "// Do not edit manually; regenerate with \"npm run openapi:generate\".",
    "",
    typeOutput.trimEnd(),
    "",
  ].join("\n");

  await writeFile(typesOutput, generatedTypes, "utf8");
  await unlink(tempTypesOutput);

  const generatedClient = [
    "// This file is generated from docs/architecture/catalogographic-openapi-v1-m0804.json.",
    "// Do not edit manually; regenerate with \"npm run openapi:generate\".",
    "",
    "import createClient from \"openapi-fetch\";",
    "import type { components, paths } from \"./catalogographic-openapi\";",
    "",
    "export type CatalogographicPaths = paths;",
    "export type CatalogographicComponents = components;",
    "",
    "export const createCatalogographicClient = (baseUrl: string | URL = process.env.NEXT_PUBLIC_API_URL ?? \"http://localhost:8000\") => createClient<CatalogographicPaths>({",
    "  baseUrl: typeof baseUrl === \"string\" ? baseUrl : baseUrl.toString(),",
    "  fetch: (input: Request) => {",
    "    const headers = new Headers(input.headers);",
    "    headers.set(\"Accept\", \"application/json\");",
    "    return fetch(new Request(input, { headers, cache: \"no-store\" }));",
    "  },",
    "});",
    "",
    "export const catalogographicClient = createCatalogographicClient();",
    "",
    "export type CatalogographicClient = typeof catalogographicClient;",
    "",
  ].join("\n");

  return { types: generatedTypes, client: generatedClient };
}

export async function generateCatalogographicClient() {
  const artifacts = await buildSchemaClientArtifacts();
  await mkdir(dirname(typesOutput), { recursive: true });
  await writeFile(typesOutput, artifacts.types, "utf8");
  await writeFile(clientOutput, artifacts.client, "utf8");
}

async function main() {
  const isCheck = process.argv.includes("--check");
  const artifacts = await buildSchemaClientArtifacts();
  for (const [target, expected] of [
    [typesOutput, artifacts.types],
    [clientOutput, artifacts.client],
  ] as const) {
    try {
      const current = await readFile(target, "utf8");
      if (current === expected) continue;
      if (isCheck) {
        console.error(`OpenAPI client drift detected in ${target}.`);
        console.error(formatUnifiedDiff(expected, current, target));
        process.exitCode = 1;
        return;
      }
      await writeFile(target, expected, "utf8");
    } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT") {
        if (isCheck) {
          console.error(`Missing generated client artifact: ${target}`);
          process.exitCode = 1;
          return;
        }
        await writeFile(target, expected, "utf8");
        continue;
      }
      throw error;
    }
  }

  if (!isCheck) {
    console.log("Catalogographic OpenAPI client generated successfully.");
    return;
  }

  console.log("Catalogographic OpenAPI client is up to date.");
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error: unknown) => {
    console.error(error);
    process.exitCode = 1;
  });
}
