import assert from "node:assert/strict";
import { test } from "node:test";
import { createPayload, draftFromWork, workPatch, parseEditorPayload, reconcileWork, equalValue } from "../src/catalog/work-editor";
import { workErrorFeedback, workValidationFeedback } from "../src/server/catalog/problems";
import { messages } from "../src/i18n/messages";
import { richWork } from "./work-editor-fixture";

test("typed draft retains repeated titles, languages, references, roles and nested notes without sharing input", () => {
  const draft = draftFromWork(richWork);
  for (const field of ["title", "language", "contribution", "subject", "note"] as const) assert.deepEqual(draft[field], richWork[field]);
  assert.deepEqual(workPatch(draft, draftFromWork(richWork)), {});
  draft.title[0].main_title[0].value = "Editado";
  const patch = workPatch(draftFromWork(richWork), draft);
  assert.deepEqual(Object.keys(patch), ["title"]);
  assert.equal(richWork.title[0].main_title[0].value, "Obra");
  assert.deepEqual(patch.title?.[0].main_title[1], { value: "Work", language: "en" });
  assert.deepEqual(patch.title?.[1], richWork.title[1]);
  assert.equal("classification" in patch, false);
  assert.equal("internal_id" in patch, false);
});

test("unrendered structures remain omitted; clearing an optional field sends []", () => {
  const base = draftFromWork(richWork), draft = structuredClone(base);
  draft.language = []; draft.note[0].label[0].value = "Nova nota";
  assert.deepEqual(workPatch(base, draft), { language: [], note: draft.note });
  assert.deepEqual(draft.note[0].note_type, ["bibliography", "summary"]);
  assert.deepEqual(draft.note[0].label[1], { value: "Note", language: "en" });
  assert.deepEqual(createPayload(draft).contribution, richWork.contribution);
});

test("boundary preserves omission/null/[] and rejects managed or unsupported fields", () => {
  for (const patch of [{}, { note: null }, { note: [] }, { subject: richWork.subject }])
    assert.deepEqual(parseEditorPayload(JSON.stringify(patch), "edit"), patch);
  for (const patch of [{ title: null }, { uri: "forged" }, { actor_id: "forged" }, { profile: "v2" }, { classification: [] }, [], { title: "flat" }])
    assert.throws(() => parseEditorPayload(JSON.stringify(patch), "edit"));
  assert.throws(() => parseEditorPayload("{}", "create"));
  const draft = draftFromWork(); draft.title[0].main_title[0].value = "New work";
  assert.deepEqual(parseEditorPayload(JSON.stringify(createPayload(draft)), "create"), draft);
});

test("412 reconciliation requires explicit choices, preserves remote independent edits and never mutates either version", () => {
  const base = draftFromWork(richWork), local = structuredClone(base), remote = structuredClone(base);
  local.title[0].main_title[0].value = "Local";
  remote.title[0].main_title[0].value = "Remote";
  remote.note = [{ label: [{ value: "Concurrent note", language: "en" }] }];
  assert.throws(() => reconcileWork(base, local, remote, {}));
  const chosen = reconcileWork(base, local, remote, { title: "local" });
  assert.deepEqual(workPatch(remote, chosen), { title: local.title });
  assert.deepEqual(chosen.note, remote.note);
  assert.equal(remote.title[0].main_title[0].value, "Remote");
  assert.equal(local.title[0].main_title[0].value, "Local");
  assert.deepEqual(reconcileWork(base, local, remote, { title: "remote" }), remote);
});

test("comparison ignores object key order, keeps array order and distinguishes null/absence", () => {
  assert.equal(equalValue({ uri: "a", kind: "local" }, { kind: "local", uri: "a" }), true);
  assert.equal(equalValue([1, 2], [2, 1]), false);
  assert.equal(equalValue({}, { note: null }), false);
});

for (const locale of ["pt", "en"] as const) {
  test(`actionable status and safe diagnostics in ${locale}`, () => {
    const feedback = [401, 403, 409, 412, 422, 503].map(status => workErrorFeedback(status, { detail: "access-secret", csrf: "secret", trace: "secret" }, locale));
    assert.equal(new Set(feedback.map(item => item.message)).size, 6);
    assert.doesNotMatch(JSON.stringify(feedback), /secret/);
    const nested = workValidationFeedback({ validation: { issues: [{ field_ids: ["/title/0/main_title/0/value"], message: "Invalid" }, { field_ids: ["ambiguous", "field"], message: "Global" }] } }, locale);
    assert.deepEqual(nested.fieldErrors, { "/title/0/main_title/0/value": "Invalid" });
    assert.equal(nested.issues.length, 2);
    const keys = Object.keys(messages.pt).filter(key => key.startsWith("workEditor"));
    assert.deepEqual(keys, Object.keys(messages.en).filter(key => key.startsWith("workEditor")));
  });
}
