import "server-only";
import { headers } from "next/headers";
import { createCatalogographicClient } from "../../generated/catalogographic-client";
import type { components } from "../../generated/catalogographic-openapi";
import { authService } from "../auth/runtime";
import { assertSameOrigin, sessionCookie, uniqueCookie, validId } from "../auth/security";
import type { AuthService, Identity } from "../auth/service";
import { currentCorrelation, withCorrelation } from "../security-audit";

export type WorkRead = components["schemas"]["WorkRead"];
export type WorkCreate = components["schemas"]["WorkCreate"];
export type WorkUpdate = components["schemas"]["WorkUpdate"];
export type WorkListPage = components["schemas"]["CatalogPage_WorkRead_"];

export interface WorkEnvelope {
  work: WorkRead;
  etag: string | null;
  metadata?: {
    editorialState: components["schemas"]["EditorialState"] | null;
    candidateState: components["schemas"]["EditorialState"] | null;
    revision: number;
    profile: string;
  };
}

export interface ListWorksQuery {
  limit?: number;
  cursor?: string | null;
  internal_id?: string | null;
  uri?: string | null;
  profile?: string | null;
  editorial_state?: "draft" | "in_review" | "approved" | "published" | "withdrawn" | "archived" | null;
}

export type CatalogPermission =
  | "authority:read"
  | "bibliographic:read"
  | "bibliographic:create"
  | "bibliographic:update";

export interface CatalogRequestOptions {
  service?: AuthService;
  sessionId?: string;
  requestHeaders?: Headers;
  ifMatch?: string;
}

export interface CatalogSessionContext {
  sessionId: string;
  accessToken: string;
  csrf: string;
  identity: Identity;
  requestId: string;
}

export class CatalogAccessError extends Error {
  constructor(
    public readonly status: number,
    public readonly code:
      | "session_missing"
      | "session_expired"
      | "permission_denied"
      | "not_found"
      | "validation_error"
      | "conflict"
      | "precondition_failed"
      | "api_unavailable",
    public readonly detail?: unknown,
  ) {
    super(
      code === "permission_denied"
        ? "Catalog access denied"
        : code === "session_missing" || code === "session_expired"
          ? "Catalog session unavailable"
          : code === "api_unavailable"
            ? "Catalog API unavailable"
            : "Catalog request failed",
    );
  }
}

const catalogPermissions: Record<"read" | "create" | "update", readonly string[]> = {
  read: ["bibliographic:read"],
  create: ["bibliographic:create"],
  update: ["bibliographic:update"],
};

function hasPermission(identity: Identity, required: readonly string[]): boolean {
  return required.every((permission) => identity.permissions.includes(permission));
}

async function resolveSessionContext(options: CatalogRequestOptions = {}): Promise<CatalogSessionContext> {
  const service = options.service ?? authService();
  let sessionId = options.sessionId;
  if (!sessionId) {
    const direct = options.requestHeaders ?? (() => {
      try {
        return headers();
      } catch {
        return new Headers();
      }
    })();
    const incoming = await Promise.resolve(direct);
    sessionId = uniqueCookie(incoming, sessionCookie(service.config));
  }

  if (!validId(sessionId)) {
    throw new CatalogAccessError(401, "session_missing");
  }

  const resolution = await withCorrelation(currentCorrelation(), () => service.resolveSession(sessionId));
  if (resolution.status === "authenticated") {
    return {
      sessionId,
      accessToken: resolution.accessToken,
      csrf: resolution.csrf,
      identity: resolution.identity,
      requestId: currentCorrelation(),
    };
  }
  if (resolution.status === "expired") {
    throw new CatalogAccessError(401, "session_expired", resolution.csrf ? { csrf: resolution.csrf } : undefined);
  }
  if (resolution.status === "forbidden") {
    throw new CatalogAccessError(403, "permission_denied", resolution.csrf ? { csrf: resolution.csrf } : undefined);
  }
  if (resolution.status === "anonymous") {
    throw new CatalogAccessError(401, "session_missing");
  }
  throw new CatalogAccessError(503, "api_unavailable");
}

function mapCatalogFailure(status: number, detail?: unknown): CatalogAccessError {
  if (status === 401) return new CatalogAccessError(401, "session_expired", detail ?? "Authentication required");
  if (status === 403) return new CatalogAccessError(403, "permission_denied", detail ?? "Permission denied");
  if (status === 404) return new CatalogAccessError(404, "not_found", detail ?? "Resource not found");
  if (status === 409) return new CatalogAccessError(409, "conflict", detail ?? "Revision conflict");
  if (status === 412) return new CatalogAccessError(412, "precondition_failed", detail ?? "Precondition failed");
  if (status === 422) return new CatalogAccessError(422, "validation_error", detail ?? "Invalid catalog payload");
  if (status === 503) return new CatalogAccessError(503, "api_unavailable", detail ?? "Catalog service unavailable");
  if (status === 504) return new CatalogAccessError(504, "api_unavailable", detail ?? "Catalog request timed out");
  return new CatalogAccessError(status || 500, "api_unavailable", detail ?? "Catalog request failed");
}

export async function executeCatalogRequest<T>(
  request: (client: ReturnType<typeof createCatalogographicClient>, context: CatalogSessionContext) => Promise<{ data?: T; error?: unknown; response?: Response }>,
  options: CatalogRequestOptions = {},
  requiredPermission: CatalogPermission,
): Promise<T> {
  const context = await resolveSessionContext(options);
  const required = requiredPermission === "authority:read" ? ["authority:read", "metadata_revision:read"]
    : requiredPermission === "bibliographic:read"
    ? catalogPermissions.read
    : requiredPermission === "bibliographic:create"
      ? catalogPermissions.create
      : catalogPermissions.update;
  if (!hasPermission(context.identity, required)) {
    throw new CatalogAccessError(403, "permission_denied", context.identity);
  }

  const client = createCatalogographicClient(options.service ? options.service.config.apiUrl : authService().config.apiUrl);
  const response = await request(client, context);
  if (response.error) {
    throw mapCatalogFailure(response.response?.status ?? 500, response.error);
  }
  if (!response.data) {
    throw new CatalogAccessError(500, "api_unavailable");
  }
  return response.data as T;
}

export async function getWorkWithMetadata(id: string, options: CatalogRequestOptions = {}): Promise<WorkEnvelope> {
  const context = await resolveSessionContext(options);
  if (!hasPermission(context.identity, catalogPermissions.read)) {
    throw new CatalogAccessError(403, "permission_denied", context.identity);
  }

  const client = createCatalogographicClient(options.service ? options.service.config.apiUrl : authService().config.apiUrl);
  const result = await client.GET("/api/v1/catalog/works/{id}", {
    params: { path: { id } },
    headers: {
      Authorization: `Bearer ${context.accessToken}`,
      "X-Request-ID": context.requestId,
      "Cache-Control": "no-store, private",
      Accept: "application/json",
    },
  });

  if (result.error) {
    throw mapCatalogFailure(result.response?.status ?? 500, result.error);
  }
  if (!result.data) {
    throw new CatalogAccessError(500, "api_unavailable");
  }

  return {
    work: result.data as WorkRead,
    etag: result.response?.headers.get("ETag") ?? null,
  };
}

export async function getWork(id: string, options: CatalogRequestOptions = {}): Promise<WorkRead> {
  const { work } = await getWorkWithMetadata(id, options);
  return work;
}

export async function listWorks(query: ListWorksQuery = {}, options: CatalogRequestOptions = {}): Promise<WorkListPage> {
  return executeCatalogRequest(async (client, context) => {
    const search = Object.fromEntries(
      Object.entries({
        limit: query.limit,
        cursor: query.cursor,
        internal_id: query.internal_id,
        uri: query.uri,
        profile: query.profile,
        editorial_state: query.editorial_state,
      }).filter(([, value]) => value !== undefined && value !== null && value !== ""),
    );

    const result = await client.GET("/api/v1/catalog/works", {
      params: { query: search },
      headers: {
        Authorization: `Bearer ${context.accessToken}`,
        "X-Request-ID": context.requestId,
        "Cache-Control": "no-store, private",
        Accept: "application/json",
      },
    });
    return { data: result.data as WorkListPage, error: result.error, response: result.response };
  }, options, "bibliographic:read");
}

export async function createWork(payload: WorkCreate, options: CatalogRequestOptions = {}): Promise<WorkRead> {
  return executeCatalogRequest(async (client, context) => {
    const result = await client.POST("/api/v1/catalog/works", {
      body: payload,
      headers: {
        Authorization: `Bearer ${context.accessToken}`,
        "X-Request-ID": context.requestId,
        "Cache-Control": "no-store, private",
        Accept: "application/json",
      },
    });
    return { data: result.data as WorkRead, error: result.error, response: result.response };
  }, options, "bibliographic:create");
}

export async function updateWork(id: string, payload: WorkUpdate, options: CatalogRequestOptions = {}): Promise<WorkRead> {
  return (await updateWorkWithMetadata(id, payload, options)).work;
}

export async function updateWorkWithMetadata(id: string, payload: WorkUpdate, options: CatalogRequestOptions = {}): Promise<WorkEnvelope> {
  const ifMatch = options.ifMatch ?? "";
  if (ifMatch.trim() === "") {
    throw new CatalogAccessError(428, "precondition_failed", "ETag required");
  }
  return executeCatalogRequest(async (client, context) => {
    const result = await client.PATCH("/api/v1/catalog/works/{id}", {
      params: { path: { id }, header: { "if-match": ifMatch } },
      body: payload,
      headers: {
        Authorization: `Bearer ${context.accessToken}`,
        "X-Request-ID": context.requestId,
        "Cache-Control": "no-store, private",
        Accept: "application/json",
      },
    });
    return { data: result.data ? { work: result.data, etag: result.response.headers.get("ETag") } : undefined, error: result.error, response: result.response };
  }, options, "bibliographic:update");
}

export async function validateWork(payload: WorkCreate, options: CatalogRequestOptions = {}) {
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/catalog/{collection}/validation", {
    params: { path: { collection: "works" } },
    body: { kind: "dto", profile_release: "urn:libris:profile:monograph:v1:2", resource: payload },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId, "Cache-Control": "no-store, private" },
  }), options, "bibliographic:create");
}

export async function requireCatalogPermission(
  required: CatalogPermission,
  options: CatalogRequestOptions = {},
): Promise<CatalogSessionContext> {
  const context = await resolveSessionContext(options);
  const expected = required === "authority:read" ? ["authority:read", "metadata_revision:read"]
    : required === "bibliographic:read"
    ? catalogPermissions.read
    : required === "bibliographic:create"
      ? catalogPermissions.create
      : catalogPermissions.update;
  if (!hasPermission(context.identity, expected)) {
    throw new CatalogAccessError(403, "permission_denied", context.identity);
  }
  return context;
}


export async function requireCatalogMutationOrigin(options: CatalogRequestOptions = {}): Promise<void> {
  const config = (options.service ?? authService()).config;
  const incoming = options.requestHeaders ?? await headers();
  try { assertSameOrigin(new Request(config.origin, { headers: incoming }), config); }
  catch { throw new CatalogAccessError(403, "permission_denied"); }
}


// Enrich only an exactly matching generation. Never pair a newer editorial
// state with the content/ETag already loaded by the editor.
export async function getWorkForEditor(id: string, options: CatalogRequestOptions = {}): Promise<WorkEnvelope> {
  const envelope = await getWorkWithMetadata(id, options);
  return enrichWorkEnvelope(envelope, options);
}

export async function enrichWorkEnvelope(envelope: WorkEnvelope, options: CatalogRequestOptions = {}): Promise<WorkEnvelope> {
  try {
    const metadata = await executeCatalogRequest(async (client, context) => {
      const requestHeaders = { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId };
      const state = await client.GET("/api/v1/catalog/{collection}/{id}/editorial", {
        params: { path: { collection: "works", id: envelope.work.internal_id } }, headers: requestHeaders,
      });
      if (!state.data || state.response.headers.get("ETag") !== envelope.etag)
        return { error: state.error, response: state.response };
      const revision = await client.GET("/api/v1/catalog/{collection}/{id}/revisions/{number}", {
        params: { path: { collection: "works", id: envelope.work.internal_id, number: state.data.current_revision } }, headers: requestHeaders,
      });
      return { data: revision.data ? {
        editorialState: state.data.editorial_state, candidateState: state.data.candidate_state,
        revision: state.data.current_revision, profile: revision.data.profile,
      } : undefined, error: revision.error, response: revision.response };
    }, options, "bibliographic:read");
    return { ...envelope, metadata };
  } catch {
    // Optional metadata may require permissions beyond editing; no invented
    // state/profile, no loss of a successful save, and no raw error disclosure.
    return envelope;
  }
}
