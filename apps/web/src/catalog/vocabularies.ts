import type { components } from "../generated/catalogographic-openapi";
import type { Locale } from "../i18n/messages";

export const LC_LANGUAGES_VOCABULARY = "http://id.loc.gov/vocabulary/languages/";

export type ConceptStatus = components["schemas"]["ConceptSearchItem"]["status"];
export type VocabularyConcept = components["schemas"]["ConceptSearchItem"];
export type ConceptValidation = components["schemas"]["ConceptValidation"];
export type ConceptSearchPage = components["schemas"]["ConceptSearchPage"];
export type VocabularyPage = components["schemas"]["VocabularyPage"];
export type VocabularyReference = { uri: string } & Record<string, unknown>;

export type ConceptSearchParams = {
  q?: string;
  uri?: string;
};

export type VocabularySearchQuery = ConceptSearchParams & {
  vocabularyUri: string;
  locale: Locale;
  limit?: number;
  offset?: number;
};

export type VocabularySearchResult = {
  ok: boolean;
  status?: number;
  message?: string;
  requestId?: string;
  page?: ConceptSearchPage;
};

export type VocabularyResolveResult = {
  ok: boolean;
  status?: number;
  message?: string;
  requestId?: string;
  state: SelectedConceptState;
};

export type SelectedConceptState =
  | { kind: "pending" }
  | { kind: "resolved"; concept: VocabularyConcept }
  | { kind: "deprecated"; concept: VocabularyConcept }
  | { kind: "unverified"; concept?: VocabularyConcept }
  | { kind: "unrecognized" }
  | { kind: "unavailable"; concept?: VocabularyConcept };

export function isAbsoluteIri(value: string): boolean {
  return /^[a-z][a-z0-9+.-]*:/i.test(value.trim()) && !/\s/.test(value.trim());
}

export function conceptSearchParams(raw: string): ConceptSearchParams | undefined {
  const normalized = raw.normalize("NFC").trim().replace(/\s+/g, " ");
  if (!normalized) return undefined;
  const term = normalized.length > 200 ? normalized.slice(0, 200) : normalized;
  if (isAbsoluteIri(term)) return { uri: term };
  return { q: term };
}

export function applySelectedConcept<T extends { uri: string }>(current: T | undefined, uri: string): T {
  return { ...(current ?? ({ uri } as T)), uri };
}

export function conceptPresentation(concept: Pick<VocabularyConcept, "label" | "notation" | "uri">): string {
  if (concept.label && concept.notation) return `${concept.label} (${concept.notation})`;
  return concept.label ?? concept.notation ?? concept.uri;
}

export function conceptStateFromItem(concept: VocabularyConcept): SelectedConceptState {
  if (concept.status === "deprecated") return { kind: "deprecated", concept };
  if (concept.status === "unverified") return { kind: "unverified", concept };
  if (concept.status === "unavailable") return { kind: "unavailable", concept };
  if (concept.status === "invalid") return { kind: "unrecognized" };
  return { kind: "resolved", concept };
}

export function conceptStateFromValidation(
  validation: ConceptValidation,
  concept?: VocabularyConcept,
): SelectedConceptState {
  if (validation.status === "deprecated") {
    return concept ? { kind: "deprecated", concept } : { kind: "unrecognized" };
  }
  if (validation.status === "unverified") return { kind: "unverified", concept };
  if (validation.status === "unavailable") return { kind: "unavailable", concept };
  if (validation.status === "resolved" && concept) return { kind: "resolved", concept };
  return { kind: "unrecognized" };
}

export function keepResolution(
  previous: SelectedConceptState | undefined,
  next: SelectedConceptState,
): SelectedConceptState {
  if (next.kind !== "unavailable") return next;
  if (previous && previous.kind !== "pending" && previous.kind !== "unavailable") return previous;
  return next;
}

export class VocabularyQueryGate {
  private generation = 0;

  next(): number {
    this.generation += 1;
    return this.generation;
  }

  isCurrent(generation: number): boolean {
    return generation === this.generation;
  }
}
