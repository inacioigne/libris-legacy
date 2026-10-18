import type { Authority, RelationEnvelope } from "../src/catalog/authorities";
export const agent: Authority = { internal_id: "00000000-0000-4000-8000-000000000002", uri: "https://example.invalid/agents/2", kind: "Person", authorized_name: [{ value: "Alex", language: "pt" }] };
export const subject: Authority = { internal_id: "00000000-0000-4000-8000-000000000003", uri: "https://example.invalid/subjects/3", preferred_label: [{ value: "Libraries", language: "en" }], scheme: [{ uri: "urn:scheme:test" }] };
export const initialRelations: RelationEnvelope = { etag: '"r1"', labels: {}, relations: {
  source: { kind: "local", entity_type: "Work", internal_id: "00000000-0000-4000-8000-000000000001", uri: "https://example.invalid/works/1" }, links: [],
} };
