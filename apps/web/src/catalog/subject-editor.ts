import type { components } from "../generated/catalogographic-openapi";
import { isRecord } from "./work-editor";

export type SubjectCreate = components["schemas"]["SubjectCreate"];
export type SubjectRead = components["schemas"]["SubjectRead"];
export type SubjectUpdate = components["schemas"]["SubjectUpdate"];
export type SubjectDraft = {
  preferred_label: components["schemas"]["LocalizedText"][];
  alternative_label: components["schemas"]["LocalizedText"][];
  scheme: components["schemas"]["ResourceReference"][];
  broader: components["schemas"]["ResourceReference"][];
  related: components["schemas"]["ResourceReference"][];
  external_reference: components["schemas"]["ResourceReference"][];
  provenance: components["schemas"]["ResourceReference"][];
};

export const newSubjectDraft = (): SubjectDraft => ({ preferred_label: [{ value: "" }], alternative_label: [], scheme: [{ uri: "" }], broader: [], related: [], external_reference: [], provenance: [] });

export function subjectCanRoundTrip(subject: SubjectRead): boolean {
  return [...(subject.broader ?? []), ...(subject.related ?? [])].every(reference => "uri" in reference && Boolean(reference.uri));
}

export function subjectDraftFromRead(subject: SubjectRead): SubjectDraft {
  const uriOnly = (values: SubjectRead["broader"]): components["schemas"]["ResourceReference"][] =>
    (values ?? []).flatMap(reference => "uri" in reference && reference.uri ? [{ uri: reference.uri }] : []);
  return { preferred_label: subject.preferred_label, alternative_label: subject.alternative_label ?? [], scheme: subject.scheme,
    broader: uriOnly(subject.broader), related: uriOnly(subject.related), external_reference: subject.external_reference ?? [], provenance: subject.provenance ?? [] };
}

export function parseSubjectPayload(value: FormDataEntryValue | null): SubjectCreate {
  if (typeof value !== "string" || value.length > 500_000) throw new Error("Invalid payload");
  const payload: unknown = JSON.parse(value);
  const fields = Object.keys(newSubjectDraft());
  if (!isRecord(payload) || Object.keys(payload).some(key => !fields.includes(key))) throw new Error("Unsupported field");
  const texts = (values: unknown, required: boolean) => {
    if (!Array.isArray(values) || (required && values.length === 0)) throw new Error("Labels required");
    const result = values.map(entry => {
      if (!isRecord(entry) || Object.keys(entry).some(key => !["value", "language"].includes(key)) || typeof entry.value !== "string" || !entry.value.trim()
        || (entry.language != null && (typeof entry.language !== "string" || !/^[a-zA-Z]+(?:-[a-zA-Z0-9]+)*$/.test(entry.language)))) throw new Error("Invalid label");
      return { value: entry.value, ...(entry.language == null ? {} : { language: entry.language as string }) };
    });
    if (required) { const languages = result.map(item => (item.language ?? "").toLowerCase()); if (new Set(languages).size !== languages.length) throw new Error("Duplicate language"); }
    return result;
  };
  const references = (values: unknown, required = false): components["schemas"]["ResourceReference"][] => {
    if (!Array.isArray(values) || (required && values.length === 0)) throw new Error("References required");
    return values.map(entry => { if (!isRecord(entry) || Object.keys(entry).some(key => key !== "uri") || typeof entry.uri !== "string" || !entry.uri.trim()) throw new Error("Invalid URI"); return { uri: entry.uri }; });
  };
  return { preferred_label: texts(payload.preferred_label, true), alternative_label: texts(payload.alternative_label ?? [], false), scheme: references(payload.scheme, true),
    broader: references(payload.broader ?? []), related: references(payload.related ?? []), external_reference: references(payload.external_reference ?? []), provenance: references(payload.provenance ?? []) };
}
