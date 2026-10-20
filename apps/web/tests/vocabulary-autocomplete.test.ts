import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applySelectedConcept,
  conceptPresentation,
  conceptSearchParams,
  conceptStateFromItem,
  conceptStateFromValidation,
  isAbsoluteIri,
  keepResolution,
  LC_LANGUAGES_VOCABULARY,
  VocabularyQueryGate,
  type ConceptValidation,
  type VocabularyConcept,
} from "../src/catalog/vocabularies";
import { createPayload, draftFromWork, workPatch } from "../src/catalog/work-editor";
import { richWork } from "./work-editor-fixture";

test("vocabulary canonical identity and query parameter extraction", () => {
  assert.equal(LC_LANGUAGES_VOCABULARY, "http://id.loc.gov/vocabulary/languages/");
  assert.equal(isAbsoluteIri("http://id.loc.gov/vocabulary/languages/por"), true);
  assert.equal(isAbsoluteIri("urn:isbn:9780141036144"), true);
  assert.equal(isAbsoluteIri("português"), false);
  assert.equal(isAbsoluteIri("   "), false);

  assert.equal(conceptSearchParams("   "), undefined);
  assert.deepEqual(conceptSearchParams("  português  "), { q: "português" });
  assert.deepEqual(conceptSearchParams("http://id.loc.gov/vocabulary/languages/eng"), {
    uri: "http://id.loc.gov/vocabulary/languages/eng",
  });
});

test("conceptPresentation formats labels, notations, and URIs deterministically", () => {
  assert.equal(
    conceptPresentation({ label: "Portuguese", notation: "por", uri: "http://id.loc.gov/vocabulary/languages/por" }),
    "Portuguese (por)",
  );
  assert.equal(
    conceptPresentation({ label: "Portuguese", notation: null, uri: "http://id.loc.gov/vocabulary/languages/por" }),
    "Portuguese",
  );
  assert.equal(
    conceptPresentation({ label: null, notation: "por", uri: "http://id.loc.gov/vocabulary/languages/por" }),
    "por",
  );
  assert.equal(
    conceptPresentation({ label: null, notation: null, uri: "http://id.loc.gov/vocabulary/languages/por" }),
    "http://id.loc.gov/vocabulary/languages/por",
  );
});

test("concept resolution state transitions and resilience against transient unavailability", () => {
  const item: VocabularyConcept = {
    concept_id: "00000000-0000-4000-8000-000000000001",
    release_id: "00000000-0000-4000-8000-000000000003",
    release_key: "lc-languages-2026-10",
    uri: "http://id.loc.gov/vocabulary/languages/por",
    label: "Portuguese",
    language: "en",
    notation: "por",
    status: "resolved",
    provenance: {
      completeness: "complete",
      provider_uri: "http://id.loc.gov",
      release_id: "00000000-0000-4000-8000-000000000003",
      release_key: "lc-languages-2026-10",
      retrieved_at: "2026-10-19T18:00:00Z",
      source_uri: LC_LANGUAGES_VOCABULARY,
      source_version: null,
      vocabulary_source: "Library of Congress",
    },
    vocabulary: {
      id: "00000000-0000-4000-8000-000000000002",
      name: "LC Languages",
      uri: LC_LANGUAGES_VOCABULARY,
    },
  };

  const resolved = conceptStateFromItem(item);
  assert.deepEqual(resolved, { kind: "resolved", concept: item });

  const deprecated = conceptStateFromItem({ ...item, status: "deprecated" });
  assert.deepEqual(deprecated, { kind: "deprecated", concept: { ...item, status: "deprecated" } });

  const invalidValidation: ConceptValidation = {
    uri: "http://example.invalid/not-a-language",
    status: "invalid",
    valid: false,
    belongs: false,
    diagnostics: [],
    provenance: null,
    release_id: null,
    release_key: null,
    replacement_uri: null,
    vocabulary: {
      id: "00000000-0000-4000-8000-000000000002",
      name: "LC Languages",
      uri: LC_LANGUAGES_VOCABULARY,
    },
  };
  assert.deepEqual(conceptStateFromValidation(invalidValidation), { kind: "unrecognized" });

  // Transient unavailability retains previously resolved concept
  const kept = keepResolution(resolved, { kind: "unavailable" });
  assert.deepEqual(kept, resolved);

  // Unavailability with no prior state remains unavailable
  const fallback = keepResolution(undefined, { kind: "unavailable" });
  assert.deepEqual(fallback, { kind: "unavailable" });
});

test("VocabularyQueryGate isolates consecutive search requests", () => {
  const gate = new VocabularyQueryGate();
  const first = gate.next();
  assert.equal(gate.isCurrent(first), true);
  const second = gate.next();
  assert.equal(gate.isCurrent(first), false);
  assert.equal(gate.isCurrent(second), true);
});

test("Work language draft retention and patch with vocabulary concept selection", () => {
  const draft = draftFromWork(richWork);
  assert.deepEqual(draft.language, [
    { uri: "http://id.loc.gov/vocabulary/languages/por" },
    { uri: "http://id.loc.gov/vocabulary/languages/eng" },
  ]);

  // Select a new language concept
  const updatedLanguages = [
    ...draft.language,
    applySelectedConcept(undefined, "http://id.loc.gov/vocabulary/languages/spa"),
  ];
  draft.language = updatedLanguages;

  const patch = workPatch(draftFromWork(richWork), draft);
  assert.deepEqual(patch.language, [
    { uri: "http://id.loc.gov/vocabulary/languages/por" },
    { uri: "http://id.loc.gov/vocabulary/languages/eng" },
    { uri: "http://id.loc.gov/vocabulary/languages/spa" },
  ]);

  // Removing all languages sets language: [] in patch
  draft.language = [];
  const emptyPatch = workPatch(draftFromWork(richWork), draft);
  assert.deepEqual(emptyPatch.language, []);

  // createPayload preserves language URIs
  const created = createPayload(draftFromWork(richWork));
  assert.deepEqual(created.language, [
    { uri: "http://id.loc.gov/vocabulary/languages/por" },
    { uri: "http://id.loc.gov/vocabulary/languages/eng" },
  ]);
});
