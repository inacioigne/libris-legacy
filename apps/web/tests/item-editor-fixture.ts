import type { ItemRead } from "../src/catalog/item-editor";
import { richInstance } from "./instance-editor-fixture";
export const richItem: ItemRead = {
  internal_id: "00000000-0000-4000-8000-000000000003", uri: "urn:libris:test:item:3",
  item_of: { uri: richInstance.uri },
  identified_by: [{ kind: "Local", value: "ITEM-3", source: [{ uri: "urn:source:catalog" }] }, { kind: "Identifier", value: "Other identifier" }],
  custodial_history: [{ value: "Doação", language: "pt" }, { value: "Donation", language: "en" }],
  note: [{ label: [{ value: "Nota", language: "pt" }, { value: "Note", language: "en" }], note_type: ["summary"] }],
  held_by: [{ kind: "external", entity_type: "Agent", uri: "urn:institution:1" }],
  shelf_mark: [{ value: "Existing semantic mark" }], provenance: [{ uri: "urn:source:provenance" }],
};
