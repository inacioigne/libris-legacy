import type { InstanceRead } from "../src/catalog/instance-editor";
export const richInstance: InstanceRead = {
  internal_id: "00000000-0000-4000-8000-000000000002", uri: "https://catalog.example.invalid/instances/2",
  instance_of: { uri: "https://catalog.example.invalid/works/1" },
  title: [{ kind: "Title", main_title: [{ value: "Edição", language: "pt" }, { value: "Edition", language: "en" }], subtitle: [{ value: "Subtitle" }] }],
  edition_statement: [{ value: "Segunda edição", language: "pt" }, { value: "Second edition", language: "en" }],
  provision_activity: [{ agent: [{ kind: "external", entity_type: "Agent", uri: "urn:publisher", namespace: "urn:publishers", external_id: "publisher" }], place: [{ uri: "urn:place" }], date: [{ value: "2026", datatype: "http://www.w3.org/2001/XMLSchema#gYear" }, { value: "[2025?]", datatype: "http://www.w3.org/2001/XMLSchema#string" }] }],
  identified_by: [{ kind: "Isbn", value: "9780000000002", source: [{ uri: "urn:source" }] }, { kind: "Local", value: "catalog-2", source: [] }],
  extent: [{ label: [{ value: "120 páginas", language: "pt" }, { value: "120 pages", language: "en" }] }],
  carrier: [{ uri: "http://id.loc.gov/vocabulary/carriers/nc" }],
  note: [{ label: [{ value: "Nota", language: "pt" }, { value: "Note", language: "en" }], note_type: ["summary", "bibliography"] }],
};
