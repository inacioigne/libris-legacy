import "server-only";
import type { components } from "../../generated/catalogographic-openapi";
import { CatalogAccessError, executeCatalogRequest, type CatalogRequestOptions } from "./index";
import { getInstance, listInstances } from "./instances";
import type { ItemRead } from "../../catalog/item-editor";

export type RelatedReference = components["schemas"]["LocalReference"];
export async function relatedChildren(collection: "works" | "instances", id: string, options: CatalogRequestOptions = {}): Promise<RelatedReference[]> {
  const relations = await executeCatalogRequest(async (client, context) => client.GET("/api/v1/catalog/{collection}/{id}/relationships", {
    params: { path: { collection, id }, query: { include_inverses: true } },
    headers: { Authorization: `Bearer ${context.accessToken}`, "X-Request-ID": context.requestId },
  }), options, "bibliographic:read");
  const predicate = `http://id.loc.gov/ontologies/bibframe/${collection === "works" ? "hasInstance" : "hasItem"}`;
  const entity = collection === "works" ? "Instance" : "Item";
  if (relations.source.internal_id !== id) throw new CatalogAccessError(503, "api_unavailable");
  return (relations.links ?? []).flatMap(link => link.predicate === predicate && link.target.kind === "local" && link.target.entity_type === entity && link.target.internal_id ? [link.target] : []);
}

export async function resolveItemInstance(reference: ItemRead["item_of"], options: CatalogRequestOptions = {}) {
  if ("kind" in reference && reference.kind === "local" && reference.internal_id) {
    const { instance } = await getInstance(reference.internal_id, options);
    return !reference.uri || instance.uri === reference.uri ? instance : undefined;
  }
  if ("uri" in reference && reference.uri) return (await listInstances({ uri: reference.uri, limit: 1 }, options)).items[0];
  return undefined;
}
