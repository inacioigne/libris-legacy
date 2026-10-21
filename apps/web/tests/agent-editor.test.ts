import assert from "node:assert/strict";
import { test } from "node:test";
import { agentCanRoundTrip, agentDraftFromRead, agentPatch, newAgentDraft, parseAgentPayload } from "../src/catalog/agent-editor";

test("Agent form preserves multilingual names, repeated variants, identifiers and exact URIs", () => {
  const payload = { ...newAgentDraft(), kind: "Organization", authorized_name: [{ value: "Biblioteca", language: "pt-BR" }, { value: "Library", language: "en" }],
    variant_name: [{ value: "Biblioteca A", language: "pt" }, { value: "Biblioteca B", language: "pt" }],
    identified_by: [{ kind: "Local", value: "AUTH-001", source: [{ uri: "urn:source:local" }] }],
    external_reference: [{ uri: "https://example.invalid/agent/1" }], provenance: [{ uri: "urn:source:1" }], related_authority: [{ uri: "urn:agent:2" }] };
  assert.deepEqual(parseAgentPayload(JSON.stringify(payload)), payload);
});

for (const kind of ["Agent", "Person", "Organization"]) test(`Agent creation accepts kind ${kind} and optional fields omitted`, () => {
  const payload = parseAgentPayload(JSON.stringify({ kind, authorized_name: [{ value: "Name" }] }));
  assert.equal(payload.kind, kind);
  assert.deepEqual(payload.authorized_name, [{ value: "Name" }]);
});

for (const payload of [
  {}, { kind: "Subject", authorized_name: [{ value: "Name" }] },
  { kind: ["Person"], authorized_name: [{ value: "Name" }] },
  { kind: "Person", authorized_name: [] },
  { kind: "Person", authorized_name: [{ value: " " }] },
  { kind: "Person", authorized_name: [{ value: "Name", language: "pt_BR" }] },
  { kind: "Person", authorized_name: [{ value: "A", language: "pt" }, { value: "B", language: "PT" }] },
  { kind: "Person", authorized_name: [{ value: "A" }, { value: "B", language: null }] },
  { kind: "Person", authorized_name: [{ value: "A" }], actor_id: "forged" },
  { kind: "Person", authorized_name: [{ value: "A" }], internal_id: "forged" },
  { kind: "Person", authorized_name: [{ value: "A" }], identified_by: [{ kind: "Local", value: "" }] },
  { kind: "Person", authorized_name: [{ value: "A" }], provenance: [{ uri: "" }] },
  { kind: "Person", authorized_name: [{ value: "A" }], variant_name: null },
  { kind: "Person", authorized_name: [{ value: "A" }], identified_by: null },
  { kind: "Person", authorized_name: [{ value: "A" }], related_authority: [{ kind: "unreconciled", label: "A" }] },
]) test(`Agent form rejects invalid payload ${JSON.stringify(payload)}`, () => {
  assert.throws(() => parseAgentPayload(JSON.stringify(payload)));
});

test("Agent form rejects malformed, file and oversized inputs", () => {
  for (const input of [null, "{", "x".repeat(500_001), new File(["{}"], "input.json")]) {
    assert.throws(() => parseAgentPayload(input));
  }
});

test("Agent editing preserves supported fields, keeps identity immutable and detects structured references", () => {
  const agent = {
    internal_id: "00000000-0000-4000-8000-000000000001", uri: "urn:libris:agent:1", kind: "Person" as const,
    authorized_name: [{ value: "Ada" }], variant_name: [{ value: "A. Lovelace", language: "en" }],
    identified_by: [{ kind: "Local" as const, value: "AUTH-1", source: [] }],
    external_reference: [{ uri: "https://example.invalid/ada" }], provenance: [{ uri: "urn:source:test" }], related_authority: [{ uri: "urn:agent:2" }],
  };
  const draft = agentDraftFromRead(agent);
  assert.equal(agentCanRoundTrip(agent), true);
  assert.deepEqual(agentPatch(draft, { ...draft, authorized_name: [{ value: "Augusta Ada" }] }), { authorized_name: [{ value: "Augusta Ada" }] });
  assert.equal("internal_id" in agentPatch(draft, draft), false);
  assert.equal("uri" in agentPatch(draft, draft), false);
  assert.equal("kind" in agentPatch(draft, draft), false);
  assert.equal(agentCanRoundTrip({ ...agent, related_authority: [{ kind: "unreconciled", entity_type: "Agent", namespace: "legacy", key: "2", label: "Unknown" }] }), false);
});
