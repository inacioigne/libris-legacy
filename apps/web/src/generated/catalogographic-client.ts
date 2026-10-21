// This file is generated from docs/architecture/catalogographic-openapi-v2-workspace.json.
// Do not edit manually; regenerate with "npm run openapi:generate".

import createClient from "openapi-fetch";
import type { components, paths } from "./catalogographic-openapi";

export type CatalogographicPaths = paths;
export type CatalogographicComponents = components;

export const createCatalogographicClient = (baseUrl: string | URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000") => createClient<CatalogographicPaths>({
  baseUrl: typeof baseUrl === "string" ? baseUrl : baseUrl.toString(),
  fetch: (input: Request) => {
    const headers = new Headers(input.headers);
    headers.set("Accept", "application/json");
    return fetch(new Request(input, { headers, cache: "no-store" }));
  },
});

export const catalogographicClient = createCatalogographicClient();

export type CatalogographicClient = typeof catalogographicClient;
