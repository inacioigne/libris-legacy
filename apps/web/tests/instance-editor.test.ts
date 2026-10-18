import assert from "node:assert/strict";
import { test } from "node:test";
import { draftFromInstance, instancePatch, instancePayload, parseInstancePayload, reconcileInstance } from "../src/catalog/instance-editor";
import { instanceErrorFeedback } from "../src/server/catalog/problems";
import { richInstance } from "./instance-editor-fixture";
import { messages } from "../src/i18n/messages";

test("Instance PATCH preserves Work identity, multilingual/repeated/nested values and typed dates", () => {
  const base = draftFromInstance(richInstance), draft = draftFromInstance(richInstance);
  assert.deepEqual(instancePatch(base, draft), {});
  draft.edition_statement[0].value = "Terceira edição";
  const patch = instancePatch(base, draft);
  assert.deepEqual(Object.keys(patch), ["edition_statement"]);
  assert.equal(patch.edition_statement?.[1].language, "en");
  assert.deepEqual(draft.provision_activity, richInstance.provision_activity);
  assert.deepEqual(draft.identified_by, richInstance.identified_by);
  assert.deepEqual(draft.instance_of, richInstance.instance_of);
  assert.equal(richInstance.edition_statement?.[0].value, "Segunda edição");
  assert.equal("uri" in patch, false);
});
test("Instance title is optional; creation requires an explicit existing Work; patch null/[]/omission stay distinct", () => {
  const draft = draftFromInstance();
  assert.throws(() => instancePayload(draft));
  draft.instance_of = { kind: "local", entity_type: "Work", internal_id: "00000000-0000-4000-8000-000000000001", uri: "urn:work:1" };
  assert.deepEqual(parseInstancePayload(JSON.stringify(instancePayload(draft)), "create"), draft);
  for (const patch of [{}, { title: null }, { note: [] }, { note: null }]) assert.deepEqual(parseInstancePayload(JSON.stringify(patch), "edit"), patch);
  for (const patch of [{ instance_of: null }, { instance_of: { uri: "urn:unverified" } }, { uri: "forged" }, { actor_id: "forged" }, { profile: "v2" }, []]) assert.throws(() => parseInstancePayload(JSON.stringify(patch), "edit"));
  assert.throws(() => parseInstancePayload("{}", "create"));
});
test("Instance reconciliation requires explicit choices and retains concurrent Work/reference and metadata groups", () => {
  const base = draftFromInstance(richInstance), local = structuredClone(base), remote = structuredClone(base);
  local.edition_statement = [{ value: "Local" }]; remote.instance_of = { uri: "urn:other-work" }; remote.note = [];
  assert.throws(() => reconcileInstance(base, local, remote, {}));
  const result = reconcileInstance(base, local, remote, { edition_statement: "local" });
  assert.deepEqual(result.instance_of, remote.instance_of);
  assert.deepEqual(result.note, []);
  assert.deepEqual(instancePatch(remote, result), { edition_statement: local.edition_statement });
});
for (const locale of ["pt", "en"] as const) test(`Instance feedback preserves diagnostic codes, paths, correlation and localized HTTP errors (${locale})`, () => {
  const result = instanceErrorFeedback(422, { code: "validation.failed", request_id: "00000000-0000-4000-8000-000000000001", issues: [{ code: "schema.invalid", field_ids: ["/provision_activity/0/date/0/value"], message: "Invalid date", path: "http://id.loc.gov/ontologies/bibframe/date" }] }, locale);
  assert.equal(result.fieldErrors["/provision_activity/0/date/0/value"], "Invalid date");
  assert.match(result.issues.join(" "), /schema.invalid.*bibframe\/date/);
  assert.ok(result.requestId);
  const feedback = [400, 401, 403, 404, 409, 412, 422].map(status => instanceErrorFeedback(status, { detail: "secret", actor_id: "secret" }, locale));
  assert.equal(new Set(feedback.map(item => item.message)).size, 7);
  assert.doesNotMatch(JSON.stringify(feedback), /secret/);
  assert.deepEqual(Object.keys(messages.pt).sort(), Object.keys(messages.en).sort());
});
