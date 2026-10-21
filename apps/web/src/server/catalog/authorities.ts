import "server-only";
import type { components } from "../../generated/catalogographic-openapi";
import type { Authority, AuthorityPage, AuthorityType, Link, RelationCollection, RelationEnvelope } from "../../catalog/authorities";
import { authorityLabel, authorityLinks, referenceKey } from "../../catalog/authorities";
import { CatalogAccessError, executeCatalogRequest, type CatalogRequestOptions } from "./index";

export type AuthorityQuery = { uri?: string; internal_id?: string; cursor?: string };
export type AgentEnvelope = { agent: components["schemas"]["AgentRead"]; etag: string | null };
export type SubjectEnvelope = { subject: components["schemas"]["SubjectRead"]; etag: string | null };
export async function createAgent(payload: components["schemas"]["AgentCreate"], options: CatalogRequestOptions = {}): Promise<components["schemas"]["AgentRead"]> {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/agents", {
    body: payload,
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "authority:manage");
}
export async function getAgentForEditor(id: string, options: CatalogRequestOptions = {}): Promise<AgentEnvelope> {
  return executeCatalogRequest(async (client, context) => {
    const result = await client.GET("/api/v1/catalog/agents/{id}", {
      params: { path: { id } },
      headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId, "Cache-Control": "no-store, private", Accept: "application/json" },
    });
    return { ...result, data: result.data ? { agent: result.data, etag: result.response.headers.get("ETag") } : undefined };
  }, options, "authority:read");
}
export async function updateAgent(id: string, payload: components["schemas"]["AgentUpdate"], options: CatalogRequestOptions = {}): Promise<AgentEnvelope> {
  const ifMatch = options.ifMatch;
  if (!ifMatch?.trim()) throw new CatalogAccessError(428, "precondition_failed");
  return executeCatalogRequest(async (client, context) => {
    const result = await client.PATCH("/api/v1/catalog/agents/{id}", {
      params: { path: { id }, header: { "if-match": ifMatch } }, body: payload,
      headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId, "Cache-Control": "no-store, private", Accept: "application/json" },
    });
    return { ...result, data: result.data ? { agent: result.data, etag: result.response.headers.get("ETag") } : undefined };
  }, options, "authority:manage");
}
export async function createSubject(payload: components["schemas"]["SubjectCreate"], options: CatalogRequestOptions = {}): Promise<components["schemas"]["SubjectRead"]> {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/subjects", { body: payload,
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId } }), options, "authority:manage");
}
export async function getSubjectForEditor(id: string, options: CatalogRequestOptions = {}): Promise<SubjectEnvelope> {
  return executeCatalogRequest(async (client, context) => { const result = await client.GET("/api/v1/catalog/subjects/{id}", { params: { path: { id } },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId, "Cache-Control": "no-store, private", Accept: "application/json" } });
    return { ...result, data: result.data ? { subject: result.data, etag: result.response.headers.get("ETag") } : undefined }; }, options, "authority:read");
}
export async function updateSubject(id: string, payload: components["schemas"]["SubjectUpdate"], options: CatalogRequestOptions = {}): Promise<SubjectEnvelope> {
  if (!options.ifMatch?.trim()) throw new CatalogAccessError(428, "precondition_failed");
  return executeCatalogRequest(async (client, context) => { const result = await client.PATCH("/api/v1/catalog/subjects/{id}", { params: { path: { id }, header: { "if-match": options.ifMatch! } }, body: payload,
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId, "Cache-Control": "no-store, private", Accept: "application/json" } });
    return { ...result, data: result.data ? { subject: result.data, etag: result.response.headers.get("ETag") } : undefined }; }, options, "authority:manage");
}
export async function listAuthorities(type: AuthorityType, query: AuthorityQuery = {}, options: CatalogRequestOptions = {}): Promise<AuthorityPage> {
  const search = Object.fromEntries(Object.entries({ uri: query.uri, internal_id: query.internal_id, cursor: query.cursor, limit: 20 }).filter(([, value]) => value !== undefined && value !== ""));
  return executeCatalogRequest(async (client, context) => client.GET(type === "Agent" ? "/api/v1/catalog/agents" : "/api/v1/catalog/subjects", {
    params: { query: search }, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "authority:read");
}
export async function getAuthority(type: AuthorityType, id: string, options: CatalogRequestOptions = {}): Promise<Authority> {
  return executeCatalogRequest(async (client, context) => {
    const result = await client.GET(type === "Agent" ? "/api/v1/catalog/agents/{id}" : "/api/v1/catalog/subjects/{id}", {
      params: { path: { id } }, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId, Accept: "application/json" },
    });
    return { ...result, data: result.data as Authority | undefined };
  }, options, "authority:read");
}
export async function getAuthorityRelations(collection: RelationCollection, id: string, options: CatalogRequestOptions = {}): Promise<RelationEnvelope> {
  const envelope = await executeCatalogRequest(async (client, context) => {
    const result = await client.GET("/api/v1/catalog/{collection}/{id}/relationships", {
      params: { path: { collection, id } }, headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
    });
    return { ...result, data: result.data ? { relations: result.data, etag: result.response.headers.get("ETag"), labels: {} } : undefined };
  }, options, "bibliographic:read");
  if (envelope.relations.source.internal_id !== id) throw new CatalogAccessError(503, "api_unavailable");
  return enrichAuthorityLabels(envelope, options);
}
export async function enrichAuthorityLabels(envelope: RelationEnvelope, options: CatalogRequestOptions = {}): Promise<RelationEnvelope> {
  const labels: Record<string, string> = {};
  // Only explicit local identities are resolved; never fetch external URIs.
  const references = new Map(authorityLinks(envelope.relations).map(link => [referenceKey(link.target), link.target]));
  for (const [key, reference] of references) {
    if (reference.kind !== "local" || !reference.internal_id) continue;
    try {
      const authority = await getAuthority(reference.entity_type === "Agent" ? "Agent" : "Subject", reference.internal_id, options);
      if (!reference.uri || authority.uri === reference.uri) labels[key] = authorityLabel(authority);
    } catch { /* A denied/missing label never erases the stored relationship. */ }
  }
  return { ...envelope, labels };
}
export async function changeAuthorityRelation(collection: RelationCollection, id: string, op: "add" | "remove", link: Link, options: CatalogRequestOptions = {}): Promise<RelationEnvelope> {
  const ifMatch = options.ifMatch;
  if (!ifMatch?.trim()) throw new CatalogAccessError(428, "precondition_failed");
  const result = await executeCatalogRequest(async (client, context) => {
    const response = await client.PATCH("/api/v1/catalog/{collection}/{id}/relationships", {
      params: { path: { collection, id }, header: { "if-match": ifMatch } },
      body: { operations: [{ op, links: [link] }] },
      headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
    });
    return { ...response, data: response.data ? { relations: response.data, etag: response.response.headers.get("ETag"), labels: {} } : undefined };
  }, options, "bibliographic:update");
  return enrichAuthorityLabels(result, options);
}
