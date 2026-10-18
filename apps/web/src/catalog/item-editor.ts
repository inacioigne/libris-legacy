import type { components } from "../generated/catalogographic-openapi";
import { equalValue, isRecord } from "./work-editor";

export type ItemRead = components["schemas"]["ItemRead"];
export type ItemCreate = components["schemas"]["ItemCreate"];
export type ItemUpdate = components["schemas"]["ItemUpdate"];
// Deliberately bounded editing surface. Other groups stay in the stored graph.
export const itemFields = ["identified_by", "custodial_history", "note"] as const;
export type ItemField = typeof itemFields[number];
export type ItemDraft = Required<Pick<ItemCreate, ItemField>> & { item_of: ItemCreate["item_of"] | null };
export type ItemChoices = Partial<Record<ItemField, "local" | "remote">>;
export const itemProfile = "urn:libris:profile:monograph:v1:1";

export function draftFromItem(item?: ItemRead | null): ItemDraft {
  return structuredClone({ item_of: item?.item_of ?? null, identified_by: item?.identified_by ?? [],
    custodial_history: item?.custodial_history ?? [], note: item?.note ?? [] });
}
export function changedItemFields(base: ItemDraft, draft: ItemDraft): ItemField[] {
  return itemFields.filter(field => !equalValue(base[field], draft[field]));
}
export function itemPatch(base: ItemDraft, draft: ItemDraft): ItemUpdate {
  if (!equalValue(base.item_of, draft.item_of)) throw new Error("Item association is read-only");
  return structuredClone(Object.fromEntries(changedItemFields(base, draft).map(field => [field, draft[field]])));
}
export function itemPayload(draft: ItemDraft): ItemCreate {
  if (!draft.item_of) throw new Error("Instance required");
  return structuredClone({ ...draft, item_of: draft.item_of });
}
export function reconcileItem(base: ItemDraft, local: ItemDraft, remote: ItemDraft, choices: ItemChoices): ItemDraft {
  if (!equalValue(base.item_of, remote.item_of)) throw new Error("Item association changed");
  const result = structuredClone(remote);
  for (const field of changedItemFields(base, local)) {
    if (!choices[field]) throw new Error("Unresolved comparison");
    if (choices[field] === "local") Object.assign(result, { [field]: structuredClone(local[field]) });
  }
  return result;
}
export function parseItemPayload(value: FormDataEntryValue | null, mode: "create" | "edit"): ItemCreate | ItemUpdate {
  if (typeof value !== "string" || value.length > 500_000) throw new Error("Invalid payload");
  const parsed: unknown = JSON.parse(value);
  const allowed: readonly string[] = mode === "create" ? [...itemFields, "item_of"] : itemFields;
  if (!isRecord(parsed) || Object.keys(parsed).some(key => !allowed.includes(key))) throw new Error("Unsupported field");
  for (const [key, entry] of Object.entries(parsed)) {
    if (key === "item_of") {
      if (!isRecord(entry) || entry.kind !== "local" || entry.entity_type !== "Instance" || typeof entry.internal_id !== "string" || typeof entry.uri !== "string") throw new Error("Select an Instance");
    } else if (!Array.isArray(entry) && !(mode === "edit" && key !== "identified_by" && entry === null)) throw new Error("Invalid property");
  }
  if (mode === "create" && (!parsed.item_of || !Array.isArray(parsed.identified_by) || !parsed.identified_by.length)) throw new Error("Instance and identifier required");
  return parsed as ItemCreate | ItemUpdate;
}
