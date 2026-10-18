import "server-only";
import type { components } from "../../generated/catalogographic-openapi";
import { instanceProfile, type InstanceCreate, type InstanceRead, type InstanceUpdate } from "../../catalog/instance-editor";
import { CatalogAccessError, executeCatalogRequest, getWork, listWorks, type CatalogRequestOptions, type ListWorksQuery, type WorkRead } from "./index";

export type InstancePage = components["schemas"]["CatalogPage_InstanceRead_"];
export interface InstanceEnvelope {
  instance: InstanceRead;
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

export async function listInstances(query: ListWorksQuery = {}, options: CatalogRequestOptions = {}): Promise<InstancePage> {
  return executeCatalogRequest(async (client, context) => client.GET("/api/v1/catalog/instances", {
    params: { query: Object.fromEntries(Object.entries(query).filter(([, value]) => value !== null && value !== undefined && value !== "")) },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:read");
}
export async function getInstance(id: string, options: CatalogRequestOptions = {}): Promise<InstanceEnvelope> {
  return executeCatalogRequest(async (client, context) => {
    const result = await client.GET("/api/v1/catalog/instances/{id}", {
      params: { path: { id } }, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
    });
    return { ...result, data: result.data ? { instance: result.data as InstanceRead, etag: result.response.headers.get("ETag") } : undefined };
  }, options, "bibliographic:read");
}
export async function createInstance(payload: InstanceCreate, options: CatalogRequestOptions = {}): Promise<InstanceRead> {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/instances", {
    body: payload, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:create");
}
export async function updateInstance(id: string, payload: InstanceUpdate, options: CatalogRequestOptions = {}): Promise<InstanceEnvelope> {
  const ifMatch = options.ifMatch;
  if (!ifMatch?.trim()) throw new CatalogAccessError(428, "precondition_failed");
  return executeCatalogRequest(async (client, context) => {
    const result = await client.PATCH("/api/v1/catalog/instances/{id}", {
      params: { path: { id }, header: { "if-match": ifMatch } }, body: payload,
      headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
    });
    return { ...result, data: result.data ? { instance: result.data, etag: result.response.headers.get("ETag") } : undefined };
  }, options, "bibliographic:update");
}
export async function enrichInstance(envelope: InstanceEnvelope, options: CatalogRequestOptions = {}): Promise<InstanceEnvelope> {
  try {
    const metadata = await executeCatalogRequest(async (client, context) => {
      const headers = { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId };
      const path = { collection: "instances" as const, id: envelope.instance.internal_id };
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
export async function getInstanceForEditor(id: string, options: CatalogRequestOptions = {}) {
  return enrichInstance(await getInstance(id, options), options);
}
export async function validateInstance(payload: InstanceCreate, options: CatalogRequestOptions = {}) {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/{collection}/validation", {
    params: { path: { collection: "instances" } }, body: { kind: "dto", profile_release: instanceProfile, resource: payload },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:create");
}

// Exact identity lookup only; no URI parsing or inference from title/ISBN.
export async function resolveInstanceWork(reference: InstanceRead["instance_of"], options: CatalogRequestOptions = {}): Promise<WorkRead | undefined> {
  if ("kind" in reference && reference.kind === "local" && reference.internal_id) {
    const work = await getWork(reference.internal_id, options);
    return !reference.uri || reference.uri === work.uri ? work : undefined;
  }
  if ("uri" in reference && reference.uri) return (await listWorks({ uri: reference.uri, limit: 1 }, options)).items[0];
  return undefined;
}
