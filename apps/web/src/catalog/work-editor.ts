import type { components } from "../generated/catalogographic-openapi";

export type WorkRead = components["schemas"]["WorkRead"];
export type WorkCreate = components["schemas"]["WorkCreate"];
export type WorkUpdate = components["schemas"]["WorkUpdate"];
export type LocalizedText = components["schemas"]["LocalizedText"];
export const editableFields = ["title", "language", "contribution", "subject", "note"] as const;
export type EditableField = typeof editableFields[number];
export type WorkDraft = Required<Pick<WorkCreate, EditableField>>;

// Copy whole DTO structures, never flatten references, literals or auxiliary nodes.
export function draftFromWork(work?: WorkRead | null): WorkDraft {
  return structuredClone({
    title: work?.title ?? [{ kind: "Title", main_title: [{ value: "" }] }],
    language: work?.language ?? [],
    contribution: work?.contribution ?? [],
    subject: work?.subject ?? [],
    note: work?.note ?? [],
  });
}

export function equalValue(left: unknown, right: unknown): boolean {
  // Object member order is irrelevant; array order and null/absence are retained.
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right))
    return left.length === right.length && left.every((value, i) => equalValue(value, right[i]));
  if (isRecord(left) && isRecord(right)) {
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every(key =>
      Object.hasOwn(right, key) && equalValue(left[key], right[key]));
  }
  return false;
}

export function changedFields(base: WorkDraft, draft: WorkDraft): EditableField[] {
  return editableFields.filter(field => !equalValue(base[field], draft[field]));
}

export function workPatch(base: WorkDraft, draft: WorkDraft): WorkUpdate {
  const patch: WorkUpdate = {};
  for (const field of changedFields(base, draft)) {
    // Assign through a typed helper to preserve the field/value association.
    assignField(patch, draft, field);
  }
  return structuredClone(patch);
}

function assignField<K extends EditableField>(target: WorkUpdate, source: WorkDraft, key: K) {
  target[key] = source[key];
}

export function createPayload(draft: WorkDraft): WorkCreate {
  return structuredClone(draft);
}

export type ReconciliationChoices = Partial<Record<EditableField, "local" | "remote">>;
export function reconcileWork(base: WorkDraft, local: WorkDraft, remote: WorkDraft, choices: ReconciliationChoices): WorkDraft {
  const result = structuredClone(remote);
  for (const field of changedFields(base, local)) {
    if (!choices[field]) throw new Error("Unresolved comparison");
    if (choices[field] === "local") copyDraftField(result, local, field);
  }
  return result;
}
function copyDraftField<K extends EditableField>(target: WorkDraft, source: WorkDraft, key: K) {
  target[key] = structuredClone(source[key]);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// The action accepts only this editor's properties. Nested DTO validity, RDF,
// references, profiles and authorization remain the API's responsibility.
export function parseEditorPayload(value: FormDataEntryValue | null, mode: "create" | "edit"): WorkCreate | WorkUpdate {
  if (typeof value !== "string" || value.length > 500_000) throw new Error("Invalid editor payload");
  const parsed: unknown = JSON.parse(value);
  if (!isRecord(parsed) || Object.keys(parsed).some(key => !editableFields.includes(key as EditableField)))
    throw new Error("Unsupported editor field");
  for (const [key, entry] of Object.entries(parsed)) {
    if (!Array.isArray(entry) && !(mode === "edit" && key !== "title" && entry === null))
      throw new Error("Invalid editor property");
  }
  if (mode === "create" && !Array.isArray(parsed.title)) throw new Error("Missing title");
  // Untrusted nested values are forwarded to Pydantic, never used as identities
  // or authorization input. This cast is only the generated client's boundary.
  return parsed as WorkCreate | WorkUpdate;
}
