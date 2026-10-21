import assert from "node:assert/strict";
import { test } from "node:test";
import { referenceIdentity, workPresentation, workTitle } from "../src/components/catalog/WorkViews";
import { richWork } from "./work-editor-fixture";

test("Work detail projects every v1 group without flattening structured references", () => {
  const projection = JSON.stringify(workPresentation(richWork));
  for (const expected of [
    "Obra", "Work", "Variante", "Subtítulo", "pt-BR",
    "http://id.loc.gov/vocabulary/languages/por",
    "https://example.invalid/agent", "http://id.loc.gov/vocabulary/relators/aut",
    "https://example.invalid/subjects/62", "00000000-0000-4000-8000-000000000062",
    "Nota", "bibliography", "001", "https://example.invalid/scheme",
    "http://id.loc.gov/vocabulary/contentTypes/txt",
    "https://example.invalid/works/other", "https://example.invalid/works/original",
  ]) assert.match(projection, new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("Work title uses preferred titles and is localized when absent", () => {
  assert.equal(workTitle(richWork, "pt"), "Obra / Work");
  assert.equal(workTitle({ ...richWork, title: [{ kind: "VariantTitle", main_title: [{ value: "Only variant" }] }] }, "en"), "Work without a main title");
});

test("reference identity keeps local, external, URI-only and unreconciled forms distinct", () => {
  assert.equal(referenceIdentity({ uri: "urn:plain" }), "urn:plain");
  assert.equal(referenceIdentity({ kind: "local", entity_type: "Agent", internal_id: "id", uri: "urn:local" }), "urn:local — id");
  assert.equal(referenceIdentity({ kind: "external", entity_type: "Agent", external_id: "42", uri: "urn:external" }), "urn:external — 42");
  assert.equal(referenceIdentity({ kind: "unreconciled", entity_type: "Agent", label: "Name", namespace: "urn:source:", key: "42" }), "Name — urn:source:42");
});
