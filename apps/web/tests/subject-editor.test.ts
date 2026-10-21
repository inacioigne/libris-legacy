import assert from "node:assert/strict";
import { test } from "node:test";
import { newSubjectDraft, parseSubjectPayload, subjectCanRoundTrip, subjectDraftFromRead } from "../src/catalog/subject-editor";

test("Subject form preserves multilingual labels, schemes and URI relationships", () => {
  const payload = { preferred_label: [{ value: "Bibliotecas", language: "pt" }, { value: "Libraries", language: "en" }], alternative_label: [{ value: "Biblioteconomia" }], scheme: [{ uri: "urn:scheme:subjects" }], broader: [{ uri: "urn:subject:culture" }], related: [{ uri: "urn:subject:books" }], external_reference: [], provenance: [{ uri: "urn:source:test" }] };
  assert.deepEqual(parseSubjectPayload(JSON.stringify(payload)), payload);
});

test("Subject form rejects missing requirements, duplicate preferred languages and structured input", () => {
  for (const payload of [newSubjectDraft(), { preferred_label: [{ value: "A" }], scheme: [] }, { preferred_label: [{ value: "A", language: "pt" }, { value: "B", language: "PT" }], scheme: [{ uri: "urn:scheme" }] }, { preferred_label: [{ value: "A" }], scheme: [{ uri: "urn:scheme" }], broader: [{ kind: "unreconciled", label: "B" }] }]) assert.throws(() => parseSubjectPayload(JSON.stringify(payload)));
});

test("Subject editor blocks references that cannot round-trip", () => {
  const subject = { internal_id: "00000000-0000-4000-8000-000000000030", uri: "urn:subject:30", preferred_label: [{ value: "Libraries" }], scheme: [{ uri: "urn:scheme" }], broader: [{ uri: "urn:subject:1" }] };
  assert.equal(subjectCanRoundTrip(subject), true);
  assert.deepEqual(subjectDraftFromRead(subject).broader, [{ uri: "urn:subject:1" }]);
  assert.equal(subjectCanRoundTrip({ ...subject, broader: [{ kind: "unreconciled", entity_type: "Subject", namespace: "legacy", key: "1", label: "Unknown" }] }), false);
});
