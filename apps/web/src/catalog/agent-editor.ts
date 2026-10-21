import type { components } from "../generated/catalogographic-openapi";
import { isRecord } from "./work-editor";

export type AgentCreate = components["schemas"]["AgentCreate"];
export type AgentRead = components["schemas"]["AgentRead"];
export type AgentUpdate = components["schemas"]["AgentUpdate"];
export type AgentDraft = Required<Omit<AgentCreate, "related_authority">> & {
  related_authority: components["schemas"]["ResourceReference"][];
};

export function newAgentDraft(): AgentDraft {
  return { kind: "Person", authorized_name: [{ value: "" }], variant_name: [],
    identified_by: [], external_reference: [], provenance: [], related_authority: [] };
}

export function agentDraftFromRead(agent: AgentRead): AgentDraft {
  return {
    kind: agent.kind,
    authorized_name: agent.authorized_name,
    variant_name: agent.variant_name ?? [],
    identified_by: agent.identified_by ?? [],
    external_reference: agent.external_reference ?? [],
    provenance: agent.provenance ?? [],
    related_authority: (agent.related_authority ?? []).flatMap(reference =>
      "uri" in reference && reference.uri ? [{ uri: reference.uri }] : [],
    ),
  };
}

export function agentCanRoundTrip(agent: AgentRead): boolean {
  return (agent.related_authority ?? []).every(reference => "uri" in reference && Boolean(reference.uri));
}

export function agentPatch(base: AgentDraft, draft: AgentDraft): AgentUpdate {
  const patch: AgentUpdate = {};
  if (JSON.stringify(base.authorized_name) !== JSON.stringify(draft.authorized_name)) patch.authorized_name = draft.authorized_name;
  if (JSON.stringify(base.variant_name) !== JSON.stringify(draft.variant_name)) patch.variant_name = draft.variant_name;
  if (JSON.stringify(base.identified_by) !== JSON.stringify(draft.identified_by)) patch.identified_by = draft.identified_by;
  if (JSON.stringify(base.external_reference) !== JSON.stringify(draft.external_reference)) patch.external_reference = draft.external_reference;
  if (JSON.stringify(base.provenance) !== JSON.stringify(draft.provenance)) patch.provenance = draft.provenance;
  if (JSON.stringify(base.related_authority) !== JSON.stringify(draft.related_authority)) patch.related_authority = draft.related_authority;
  return patch;
}

// This creation form supports URI-only related authorities. Structured authority
// references remain available through the API and the existing linking workflow.
export function parseAgentPayload(value: FormDataEntryValue | null): AgentCreate {
  if (typeof value !== "string" || value.length > 500_000) throw new Error("Invalid payload");
  const payload: unknown = JSON.parse(value);
  const fields = Object.keys(newAgentDraft());
  if (!isRecord(payload) || Object.keys(payload).some(key => !fields.includes(key))) throw new Error("Unsupported field");
  if (payload.kind !== "Agent" && payload.kind !== "Person" && payload.kind !== "Organization") throw new Error("Invalid kind");
  function texts(values: unknown, required: boolean): components["schemas"]["LocalizedText"][] {
    if (!Array.isArray(values) || (required && values.length === 0)) throw new Error("Names required");
    return values.map((entry: unknown) => {
      if (!isRecord(entry) || Object.keys(entry).some(key => !["value", "language"].includes(key))
        || typeof entry.value !== "string" || !entry.value.trim()
        || (entry.language != null && (typeof entry.language !== "string" || !/^[a-zA-Z]+(?:-[a-zA-Z0-9]+)*$/.test(entry.language)))) throw new Error("Invalid name");
      return { value: entry.value, ...(entry.language == null ? {} : { language: entry.language as string }) };
    });
  }
  function references(values: unknown): components["schemas"]["ResourceReference"][] {
    if (!Array.isArray(values)) throw new Error("Invalid references");
    return values.map((entry: unknown) => {
      if (!isRecord(entry) || Object.keys(entry).some(key => key !== "uri") || typeof entry.uri !== "string" || !entry.uri.trim()) throw new Error("Invalid URI reference");
      return { uri: entry.uri };
    });
  }
  const names = texts(payload.authorized_name, true);
  const languages = names.map(name => (name.language ?? "").toLowerCase());
  if (new Set(languages).size !== languages.length) throw new Error("Duplicate authorized language");
  const identifiers = payload.identified_by === undefined ? [] : payload.identified_by;
  if (!Array.isArray(identifiers)) throw new Error("Invalid identifiers");
  return {
    kind: payload.kind, authorized_name: names,
    variant_name: texts(payload.variant_name === undefined ? [] : payload.variant_name, false),
    identified_by: identifiers.map((entry: unknown) => {
      if (!isRecord(entry) || Object.keys(entry).some(key => !["kind", "value", "source"].includes(key))
        || (entry.kind !== "Identifier" && entry.kind !== "Local" && entry.kind !== "Isbn")
        || typeof entry.value !== "string" || !entry.value.trim()) throw new Error("Invalid identifier");
      return { kind: entry.kind, value: entry.value, source: references(entry.source ?? []) };
    }),
    external_reference: references(payload.external_reference === undefined ? [] : payload.external_reference),
    provenance: references(payload.provenance === undefined ? [] : payload.provenance),
    related_authority: references(payload.related_authority === undefined ? [] : payload.related_authority),
  };
}
