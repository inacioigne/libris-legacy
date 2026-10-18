import assert from "node:assert/strict";
import { test } from "node:test";
import { draftFromItem, itemPatch, itemPayload, parseItemPayload, reconcileItem } from "../src/catalog/item-editor";
import { itemErrorFeedback } from "../src/server/catalog/problems";
import { richItem } from "./item-editor-fixture";
import { messages } from "../src/i18n/messages";

test("Item PATCH retains identities, repeated multilingual values and unedited groups", () => {
  const base = draftFromItem(richItem), draft = draftFromItem(richItem);
  assert.deepEqual(itemPatch(base, draft), {});
  draft.custodial_history[0].value = "Custódia revista";
  const patch = itemPatch(base, draft);
  assert.deepEqual(Object.keys(patch), ["custodial_history"]);
  assert.equal(patch.custodial_history?.[1].language, "en");
  assert.deepEqual(draft.identified_by, richItem.identified_by);
  assert.deepEqual(draft.item_of, richItem.item_of);
  assert.equal(richItem.custodial_history?.[0].value, "Doação");
  assert.equal("uri" in patch, false);
  assert.equal("held_by" in patch, false);
});
test("Item requires explicit local Instance and identifiers; PATCH null, empty and omitted stay distinct", () => {
  const draft = draftFromItem();
  assert.throws(() => itemPayload(draft));
  draft.item_of = { kind: "local", entity_type: "Instance", internal_id: "00000000-0000-4000-8000-000000000001", uri: "urn:instance:1" };
  draft.identified_by = [{ kind: "Local", value: "SEMANTIC-1" }];
  assert.deepEqual(parseItemPayload(JSON.stringify(itemPayload(draft)), "create"), draft);
  for (const patch of [{}, { note: [] }, { note: null }, { custodial_history: null }]) assert.deepEqual(parseItemPayload(JSON.stringify(patch), "edit"), patch);
  for (const patch of [{ item_of: draft.item_of }, { item_of: null }, { identified_by: null }, { uri: "forged" }, { actor_id: "forged" }, { profile: "v2" }, { held_by: [] }, []]) assert.throws(() => parseItemPayload(JSON.stringify(patch), "edit"));
  assert.throws(() => parseItemPayload("{}", "create"));
  assert.throws(() => parseItemPayload(JSON.stringify({ ...draft, item_of: { ...draft.item_of, entity_type: "Work" } }), "create"));
});
test("Item conflict reconciliation retains remote untouched groups and refuses changed Instance", () => {
  const base = draftFromItem(richItem), local = structuredClone(base), remote = structuredClone(base);
  local.custodial_history = [{ value: "Local" }]; remote.note = [];
  assert.throws(() => reconcileItem(base, local, remote, {}));
  const result = reconcileItem(base, local, remote, { custodial_history: "local" });
  assert.deepEqual(result.note, []);
  assert.deepEqual(itemPatch(remote, result), { custodial_history: local.custodial_history });
  remote.item_of = { uri: "urn:other-instance" };
  assert.throws(() => reconcileItem(base, local, remote, { custodial_history: "local" }));
  assert.throws(() => itemPatch(base, remote));
});
for (const locale of ["pt", "en"] as const) test(`Item diagnostics and errors are localized with exact field correlation (${locale})`, () => {
  const result = itemErrorFeedback(422, { code: "validation.failed", request_id: "00000000-0000-4000-8000-000000000001", issues: [{ code: "schema.invalid", field_ids: ["/identified_by/0/value"], message: "Invalid", path: "http://www.w3.org/1999/02/22-rdf-syntax-ns#value" }, { message: "Record error", field_ids: ["/held_by/0/uri"] }] }, locale);
  assert.equal(result.fieldErrors["/identified_by/0/value"], "Invalid");
  assert.equal(result.fieldErrors["/held_by/0/uri"], undefined);
  assert.equal(result.issues.length, 3);
  assert.ok(result.requestId);
  const feedback = [400, 401, 403, 404, 409, 412, 422].map(status => itemErrorFeedback(status, { detail: "secret", actor_id: "secret" }, locale));
  assert.equal(new Set(feedback.map(item => item.message)).size, 7);
  assert.doesNotMatch(JSON.stringify(feedback), /secret/);
  assert.deepEqual(Object.keys(messages.pt).sort(), Object.keys(messages.en).sort());
});
