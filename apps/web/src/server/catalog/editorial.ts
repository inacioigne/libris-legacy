import "server-only";

import type { components } from "../../generated/catalogographic-openapi";
import {
  CatalogAccessError,
  executeCatalogRequest,
  requireCatalogMutationOrigin,
  type CatalogRequestOptions,
} from "./index";

export type CatalogCollection = "works" | "instances" | "items" | "agents" | "subjects";
export type EditorialState = components["schemas"]["EditorialStateRead"];
export type RevisionPage = components["schemas"]["RevisionPage"];
export type EventPage = components["schemas"]["EventPage"];
export type SemanticDiff = components["schemas"]["SemanticDiff"];
export type EditorialAction = "submit" | "return" | "approve" | "publish" | "withdraw" | "republish" | "archive";

export interface EditorialDashboard {
  state: EditorialState;
  etag: string | null;
  revisions?: RevisionPage;
  audit?: EventPage;
}

const readPermission = (collection: CatalogCollection) =>
  collection === "agents" || collection === "subjects" ? "authority:read" as const : "bibliographic:read" as const;

const headersFor = (token: string, requestId: string) => ({
  Authorization: `Bearer ${token}`,
  "X-Request-ID": requestId,
  "Cache-Control": "no-store, private",
});

export async function getEditorialDashboard(
  collection: CatalogCollection,
  id: string,
  permissions: readonly string[],
  options: CatalogRequestOptions = {},
): Promise<EditorialDashboard> {
  return executeCatalogRequest(async (client, context) => {
    const path = { collection, id };
    const state = await client.GET("/api/v1/catalog/{collection}/{id}/editorial", {
      params: { path }, headers: headersFor(context.accessToken, context.requestId),
    });
    if (!state.data) return { error: state.error, response: state.response };

    const headers = headersFor(context.accessToken, context.requestId);
    const [revisions, audit] = await Promise.all([
      permissions.includes("metadata_revision:read")
        ? client.GET("/api/v1/catalog/{collection}/{id}/revisions", { params: { path }, headers })
        : Promise.resolve(undefined),
      permissions.includes("metadata_audit:read")
        ? client.GET("/api/v1/catalog/{collection}/{id}/audit", { params: { path }, headers })
        : Promise.resolve(undefined),
    ]);
    // The state is useful even if a separately protected historical view is denied.
    return {
      data: {
        state: state.data,
        etag: state.response.headers.get("ETag"),
        revisions: revisions?.data,
        audit: audit?.data,
      },
      response: state.response,
    };
  }, options, readPermission(collection));
}

export async function getRevisionDiff(
  collection: CatalogCollection,
  id: string,
  previous: number,
  current: number,
  options: CatalogRequestOptions = {},
): Promise<SemanticDiff> {
  return executeCatalogRequest(async (client, context) => client.GET(
    "/api/v1/catalog/{collection}/{id}/revisions/compare",
    { params: { path: { collection, id }, query: { previous, current } }, headers: headersFor(context.accessToken, context.requestId) },
  ), options, readPermission(collection));
}

export async function transitionEditorial(
  collection: CatalogCollection,
  id: string,
  action: EditorialAction,
  payload: components["schemas"]["TransitionRequest"],
  ifMatch: string,
  options: CatalogRequestOptions = {},
): Promise<{ state: EditorialState; etag: string | null }> {
  if (!ifMatch.trim()) throw new CatalogAccessError(428, "precondition_failed");
  await requireCatalogMutationOrigin(options);
  return executeCatalogRequest(async (client, context) => {
    const params = { path: { collection, id }, header: { "if-match": ifMatch } };
    const headers = headersFor(context.accessToken, context.requestId);
    const result = action === "submit" ? await client.POST("/api/v1/catalog/{collection}/{id}/editorial/submit", { params, body: payload, headers })
      : action === "return" ? await client.POST("/api/v1/catalog/{collection}/{id}/editorial/return", { params, body: payload, headers })
      : action === "approve" ? await client.POST("/api/v1/catalog/{collection}/{id}/editorial/approve", { params, body: payload, headers })
      : action === "publish" ? await client.POST("/api/v1/catalog/{collection}/{id}/editorial/publish", { params, body: payload, headers })
      : action === "withdraw" ? await client.POST("/api/v1/catalog/{collection}/{id}/editorial/withdraw", { params, body: payload, headers })
      : action === "republish" ? await client.POST("/api/v1/catalog/{collection}/{id}/editorial/republish", { params, body: payload, headers })
      : await client.POST("/api/v1/catalog/{collection}/{id}/editorial/archive", { params, body: payload, headers });
    return {
      data: result.data ? { state: result.data, etag: result.response.headers.get("ETag") } : undefined,
      error: result.error,
      response: result.response,
    };
  }, options, readPermission(collection));
}
