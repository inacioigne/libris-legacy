import type { WorkRead } from "../src/catalog/work-editor";

export const richWork: WorkRead = {
  internal_id: "00000000-0000-4000-8000-000000000061",
  uri: "https://example.invalid/works/61",
  title: [
    { kind: "Title", main_title: [{ value: "Obra", language: "pt" }, { value: "Work", language: "en" }], subtitle: [{ value: "Subtítulo", language: "pt-BR" }] },
    { kind: "VariantTitle", main_title: [{ value: "Variante", language: "pt" }] },
  ],
  language: [{ uri: "http://id.loc.gov/vocabulary/languages/por" }, { uri: "http://id.loc.gov/vocabulary/languages/eng" }],
  contribution: [{ agent: { kind: "external", entity_type: "Agent", uri: "https://example.invalid/agent", namespace: "https://example.invalid/", external_id: "agent" }, role: [{ uri: "http://id.loc.gov/vocabulary/relators/aut" }, { uri: "http://id.loc.gov/vocabulary/relators/edt" }] }],
  subject: [{ kind: "local", entity_type: "Subject", internal_id: "00000000-0000-4000-8000-000000000062", uri: "https://example.invalid/subjects/62" }],
  note: [{ label: [{ value: "Nota", language: "pt" }, { value: "Note", language: "en" }], note_type: ["bibliography", "summary"] }],
  classification: [{ classification_portion: ["001", "002"], source: [{ uri: "https://example.invalid/scheme" }] }],
  content: [{ uri: "http://id.loc.gov/vocabulary/contentTypes/txt" }],
  related_to: [{ uri: "https://example.invalid/works/other" }],
  translation_of: [{ uri: "https://example.invalid/works/original" }],
};
