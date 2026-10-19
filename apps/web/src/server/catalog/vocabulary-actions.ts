"use server";

import {
  conceptStateFromItem,
  conceptStateFromValidation,
  type VocabularyResolveResult,
  type VocabularySearchQuery,
  type VocabularySearchResult,
} from "../../catalog/vocabularies";
import { messages, type Locale } from "../../i18n/messages";
import { CatalogAccessError } from "./index";
import { resolveVocabularyConcept, searchVocabularyConcepts } from "./vocabularies";
import { workErrorFeedback } from "./problems";

function failure(error: unknown, locale: Locale): { ok: false; status: number; message: string; requestId?: string } {
  const status = error instanceof CatalogAccessError ? error.status : 503;
  const feedback = workErrorFeedback(status, error instanceof CatalogAccessError ? error.detail : undefined, locale);
  return {
    ok: false,
    status,
    message: status === 403 ? messages[locale].vocabularyPermission : status === 401 ? feedback.message : messages[locale].vocabularySearchError,
    requestId: feedback.requestId,
  };
}

function safeLocale(locale: Locale | string): Locale {
  return locale === "en" ? "en" : "pt";
}

export async function searchVocabularyConceptsAction(query: VocabularySearchQuery): Promise<VocabularySearchResult> {
  const locale = safeLocale(query.locale);
  try {
    return {
      ok: true,
      page: await searchVocabularyConcepts(query.vocabularyUri, {
        q: query.q,
        uri: query.uri,
        locale,
        limit: query.limit,
        offset: query.offset,
      }),
    };
  } catch (error) {
    return failure(error, locale);
  }
}

export async function resolveVocabularyConceptAction(
  vocabularyUri: string,
  uri: string,
  locale: Locale,
): Promise<VocabularyResolveResult> {
  const safe = safeLocale(locale);
  try {
    const resolved = await resolveVocabularyConcept(vocabularyUri, uri, safe);
    if (resolved.concept) return { ok: true, state: conceptStateFromItem(resolved.concept) };
    if (resolved.validation) return { ok: true, state: conceptStateFromValidation(resolved.validation) };
    return { ok: true, state: { kind: "unrecognized" } };
  } catch (error) {
    const result = failure(error, safe);
    return { ...result, state: { kind: error instanceof CatalogAccessError && error.status === 404 ? "unrecognized" : "unavailable" } };
  }
}
