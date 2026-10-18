import type { components } from "../generated/catalogographic-openapi";
import { equalValue, isRecord } from "./work-editor";

export type InstanceRead = components["schemas"]["InstanceRead"];
export type InstanceCreate = components["schemas"]["InstanceCreate"];
export type InstanceUpdate = components["schemas"]["InstanceUpdate"];
export const instanceFields = ["instance_of", "title", "edition_statement", "provision_activity", "identified_by", "extent", "carrier", "note"] as const;
export type InstanceField = typeof instanceFields[number];
export type InstanceDraft = Required<Omit<InstanceCreate, "instance_of">> & { instance_of: InstanceCreate["instance_of"] | null };
export type InstanceChoices = Partial<Record<InstanceField, "local" | "remote">>;
export const instanceProfile = "urn:libris:profile:monograph:v1:1";

export function draftFromInstance(instance?: InstanceRead | null): InstanceDraft {
  return structuredClone({ instance_of: instance?.instance_of ?? null, title: instance?.title ?? [],
    edition_statement: instance?.edition_statement ?? [], provision_activity: instance?.provision_activity ?? [],
    identified_by: instance?.identified_by ?? [], extent: instance?.extent ?? [], carrier: instance?.carrier ?? [], note: instance?.note ?? [] });
}
export function changedInstanceFields(base: InstanceDraft, draft: InstanceDraft): InstanceField[] {
  return instanceFields.filter(field => !equalValue(base[field], draft[field]));
}
export function instancePatch(base: InstanceDraft, draft: InstanceDraft): InstanceUpdate {
  return structuredClone(Object.fromEntries(changedInstanceFields(base, draft).map(field => [field, draft[field]]))) as InstanceUpdate;
}
export function instancePayload(draft: InstanceDraft): InstanceCreate {
  if (!draft.instance_of) throw new Error("Work required");
  return structuredClone({ ...draft, instance_of: draft.instance_of });
}
export function reconcileInstance(base: InstanceDraft, local: InstanceDraft, remote: InstanceDraft, choices: InstanceChoices): InstanceDraft {
  const result = structuredClone(remote);
  for (const field of changedInstanceFields(base, local)) {
    if (!choices[field]) throw new Error("Unresolved comparison");
    if (choices[field] === "local") Object.assign(result, { [field]: structuredClone(local[field]) });
  }
  return result;
}
export function parseInstancePayload(value: FormDataEntryValue | null, mode: "create" | "edit"): InstanceCreate | InstanceUpdate {
  if (typeof value !== "string" || value.length > 500_000) throw new Error("Invalid payload");
  const parsed: unknown = JSON.parse(value);
  if (!isRecord(parsed) || Object.keys(parsed).some(key => !instanceFields.includes(key as InstanceField))) throw new Error("Unsupported field");
  for (const [key, entry] of Object.entries(parsed)) {
    if (key === "instance_of") {
      // This UI links existing Works only. The API revalidates UUID, URI and type.
      if (!isRecord(entry) || entry.kind !== "local" || entry.entity_type !== "Work" || typeof entry.internal_id !== "string" || typeof entry.uri !== "string") throw new Error("Select a Work");
    } else if (!Array.isArray(entry) && !(mode === "edit" && entry === null)) throw new Error("Invalid property");
  }
  if (mode === "create" && !parsed.instance_of) throw new Error("Work required");
  return parsed as InstanceCreate | InstanceUpdate;
}
