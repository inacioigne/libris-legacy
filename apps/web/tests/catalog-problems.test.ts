import assert from "node:assert/strict";
import { test } from "node:test";
import { workValidationFeedback } from "../src/server/catalog/problems";

test("422 feedback keeps public fields and correlation without exposing the error payload", () => {
  const feedback = workValidationFeedback({
    request_id: "00000000-0000-4000-8000-000000000001",
    detail: "internal detail must not cross the action boundary",
    accessToken: "secret",
    issues: [{ field_ids: ["/language/0/uri"], message: "Estrutura ou valor incompatível.", input: "private input" }],
  }, "pt");
  assert.deepEqual(feedback.issues, ["/language/0/uri: Estrutura ou valor incompatível."]);
  assert.equal(feedback.requestId, "00000000-0000-4000-8000-000000000001");
  assert.match(feedback.message, /A API rejeitou/);
  assert.doesNotMatch(JSON.stringify(feedback), /secret|private input|internal detail/);
});

test("feedback handles global SHACL issues and deduplicates diagnostics", () => {
  const issue = { message: "Dados não conformes.", field_ids: [] };
  const feedback = workValidationFeedback({ issues: [issue, issue] }, "en");
  assert.deepEqual(feedback.issues, ["Dados não conformes."]);
  assert.match(feedback.message, /The API rejected/);
});

test("missing or malformed diagnostics retain a localized fallback", () => {
  for (const problem of [null, "unexpected", {}, { issues: [null, {}, { message: 42 }], request_id: "secret" }]) {
    const feedback = workValidationFeedback(problem, "pt");
    assert.deepEqual(feedback.issues, []);
    assert.equal(feedback.requestId, undefined);
    assert.match(feedback.message, /A API rejeitou/);
  }
});

test("membership, availability and deprecation diagnostics are localized without URI replacement", () => {
  const issue = {
    code: "reference.deprecated",
    message: "Mensagem padrão.",
    field_ids: ["/language"],
    path: { kind: "iri", iri: "http://id.loc.gov/ontologies/bibframe/language" },
    parameters: { replacement_uri: "http://id.loc.gov/vocabulary/languages/eng" },
  };
  const pt = workValidationFeedback({ issues: [issue] }, "pt");
  const en = workValidationFeedback({ issues: [issue] }, "en");
  assert.match(pt.issues[0], /descontinuado/);
  assert.match(pt.issues[0], /Sucessor sugerido \(não aplicado\)/);
  assert.match(pt.issues[0], /languages\/eng/);
  assert.match(en.issues[0], /deprecated/);
  assert.match(en.issues[0], /Suggested successor \(not applied\)/);
  assert.equal(pt.fieldErrors["/language"], pt.issues[0].split(": ").slice(1).join(": "));

  const mismatch = workValidationFeedback({
    issues: [{ code: "reference.vocabulary_mismatch", message: "default" }],
  }, "en");
  assert.match(mismatch.issues[0], /not a member/);
});
