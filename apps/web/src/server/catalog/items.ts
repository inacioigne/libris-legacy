import "server-only";
import type { components } from "../../generated/catalogographic-openapi";
import { itemProfile, type ItemCreate, type ItemRead, type ItemUpdate } from "../../catalog/item-editor";
import { CatalogAccessError, executeCatalogRequest, type CatalogRequestOptions, type ListWorksQuery } from "./index";

export type ItemPage = components["schemas"]["CatalogPage_ItemRead_"];
export interface ItemEnvelope {
  item: ItemRead;
  etag: string | null;
  metadata?: {
    editorialState: components["schemas"]["EditorialState"] | null;
    candidateState: components["schemas"]["EditorialState"] | null;
    revision: number;
    profile: string;
    createdAt: string;
    provenance: components["schemas"]["Provenance"];
  };
}

export async function listItems(query: ListWorksQuery = {}, options: CatalogRequestOptions = {}): Promise<ItemPage> {
  return executeCatalogRequest(async (client, context) => client.GET("/api/v1/catalog/items", {
    params: { query: Object.fromEntries(Object.entries(query).filter(([, value]) => value !== null && value !== undefined && value !== "")) },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:read");
}
export async function getItem(id: string, options: CatalogRequestOptions = {}): Promise<ItemEnvelope> {
  return executeCatalogRequest(async (client, context) => {
    const result = await client.GET("/api/v1/catalog/items/{id}", {
      params: { path: { id } }, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
    });
    return { ...result, data: result.data ? { item: result.data as ItemRead, etag: result.response.headers.get("ETag") } : undefined };
  }, options, "bibliographic:read");
}
export async function createItem(payload: ItemCreate, options: CatalogRequestOptions = {}): Promise<ItemRead> {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/items", {
    body: payload, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:create");
}
export async function updateItem(id: string, payload: ItemUpdate, options: CatalogRequestOptions = {}): Promise<ItemEnvelope> {
  const ifMatch = options.ifMatch;
  if (!ifMatch?.trim()) throw new CatalogAccessError(428, "precondition_failed");
  return executeCatalogRequest(async (client, context) => {
    const result = await client.PATCH("/api/v1/catalog/items/{id}", {
      params: { path: { id }, header: { "if-match": ifMatch } }, body: payload,
      headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
    });
    return { ...result, data: result.data ? { item: result.data, etag: result.response.headers.get("ETag") } : undefined };
  }, options, "bibliographic:update");
}
export async function enrichItem(envelope: ItemEnvelope, options: CatalogRequestOptions = {}): Promise<ItemEnvelope> {
  try {
    const metadata = await executeCatalogRequest(async (client, context) => {
      const headers = { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId };
      const path = { collection: "items" as const, id: envelope.item.internal_id };
      const state = await client.GET("/api/v1/catalog/{collection}/{id}/editorial", { params: { path }, headers });
      if (!state.data || state.response.headers.get("ETag") !== envelope.etag) return { error: state.error, response: state.response };
      const revision = await client.GET("/api/v1/catalog/{collection}/{id}/revisions/{number}", {
        params: { path: { ...path, number: state.data.current_revision } }, headers,
      });
      return { ...revision, data: revision.data ? { editorialState: state.data.editorial_state, candidateState: state.data.candidate_state,
        revision: revision.data.number, profile: revision.data.profile, createdAt: revision.data.created_at, provenance: revision.data.provenance } : undefined };
    }, options, "bibliographic:read");
    return { ...envelope, metadata };
  } catch { return envelope; } // Optional, permission-bound metadata; never replace the saved generation.
}
export async function getItemForEditor(id: string, options: CatalogRequestOptions = {}) {
  return enrichItem(await getItem(id, options), options);
}
export async function validateItem(payload: ItemCreate, options: CatalogRequestOptions = {}) {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/{collection}/validation", {
    params: { path: { collection: "items" } }, body: { kind: "dto", profile_release: itemProfile, resource: payload },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:create");
}

