import type { components } from "../generated/catalogographic-openapi";

export type AuthorityType = "Agent" | "Subject";
export type Authority = components["schemas"]["AgentRead"] | components["schemas"]["SubjectRead"];
export type Link = NonNullable<components["schemas"]["RelationshipRead"]["links"]>[number];
export type Reference = Link["target"];
export type Relations = components["schemas"]["RelationshipRead"];
export type RelationCollection = "works" | "instances" | "items";
export type RelationEnvelope = { relations: Relations; etag: string | null; labels: Record<string, string> };
export type AuthorityPage = { items: Authority[]; next_cursor?: string | null };
export const BF = "http://id.loc.gov/ontologies/bibframe/";

export function authorityLabel(authority: Authority): string {
  return ("authorized_name" in authority ? authority.authorized_name : authority.preferred_label).map(text => text.value).join(" / ");
}
export function authorityReference(authority: Authority): components["schemas"]["LocalReference"] {
  return { kind: "local", entity_type: "authorized_name" in authority ? "Agent" : "Subject", internal_id: authority.internal_id, uri: authority.uri };
}
export function referenceKey(reference: Reference): string {
  return reference.kind === "unreconciled" ? JSON.stringify([reference.namespace, reference.key]) : reference.uri ?? (reference.kind === "local" ? reference.internal_id ?? "" : "");
}
export function sameReference(left: Reference, right: Reference): boolean {
  if (left.kind === "unreconciled" || right.kind === "unreconciled") return left.kind === right.kind && referenceKey(left) === referenceKey(right);
  return Boolean(left.uri && left.uri === right.uri) || (left.kind === "local" && right.kind === "local" && Boolean(left.internal_id && left.internal_id === right.internal_id));
}
export function sameLink(left: Link, right: Link): boolean {
  return left.kind === right.kind && left.predicate === right.predicate && sameReference(left.source, right.source) && sameReference(left.target, right.target)
    && (left.kind !== "contribution" || right.kind !== "contribution" || JSON.stringify([...left.role].sort()) === JSON.stringify([...right.role].sort()))
    && (left.kind !== "publication" || right.kind !== "publication" || left.activity_key === right.activity_key);
}
export function authorityLinks(relations: Relations): Link[] {
  return (relations.links ?? []).filter(link => link.target.entity_type === "Agent" || link.target.entity_type === "Subject");
}
export function selectedLink(source: Relations["source"], authority: Authority, roles: string[], activityKey?: string): Link {
  const target = authorityReference(authority);
  if (source.entity_type === "Work") return target.entity_type === "Subject"
    ? { kind: "direct", source, target, predicate: BF + "subject" }
    : { kind: "contribution", source, target, predicate: "http://id.loc.gov/ontologies/bibframe/contribution", role: roles };
  if (source.entity_type === "Item" && "kind" in authority && authority.kind === "Organization") return { kind: "direct", source, target, predicate: BF + "heldBy" };
  if (source.entity_type === "Instance" && target.entity_type === "Agent" && activityKey) return { kind: "publication", source, target, predicate: "http://id.loc.gov/ontologies/bibframe/provisionActivity", activity_key: activityKey, activity_type: "Publication" };
  throw new Error("Unsupported authority relationship");
}
