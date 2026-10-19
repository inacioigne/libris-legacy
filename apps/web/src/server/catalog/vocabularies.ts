import "server-only";
import type { ConceptSearchPage, ConceptValidation, VocabularyConcept, VocabularyPage } from "../../catalog/vocabularies";
import { CatalogAccessError, executeCatalogRequest, type CatalogRequestOptions } from "./index";

const vocabularyIds = new Map<string, string>();

export function resetVocabularyIdCache(): void {
  vocabularyIds.clear();
}

async function listVocabularies(options: CatalogRequestOptions = {}): Promise<VocabularyPage> {
  return executeCatalogRequest(async (client, context) => client.GET("/api/v1/vocabularies", {
    headers: {
      Authorization: `Bearer ${context.accessToken}`,
      "X-Request-ID": context.requestId,
      "Cache-Control": "no-store, private",
      Accept: "application/json",
    },
  }), options, "vocabulary:read");
}

async function vocabularyId(uri: string, options: CatalogRequestOptions = {}): Promise<string> {
  const cached = vocabularyIds.get(uri);
  if (cached) return cached;
  const page = await listVocabularies(options);
  const match = page.items.find(item => item.uri === uri);
  if (!match) throw new CatalogAccessError(404, "not_found", page);
  vocabularyIds.set(uri, match.id);
  return match.id;
}

export async function searchVocabularyConcepts(
  vocabularyUri: string,
  query: { q?: string; uri?: string; locale?: string; limit?: number; offset?: number },
  options: CatalogRequestOptions = {},
): Promise<ConceptSearchPage> {
  const id = await vocabularyId(vocabularyUri, options);
  const search = Object.fromEntries(Object.entries({
    q: query.q,
    uri: query.uri,
    locale: query.locale ?? "en",
    limit: query.limit ?? 20,
    offset: query.offset ?? 0,
  }).filter(([, value]) => value !== undefined && value !== ""));
  return executeCatalogRequest(async (client, context) => client.GET("/api/v1/vocabularies/{vocabulary_id}/concepts", {
    params: { path: { vocabulary_id: id }, query: search },
    headers: {
      Authorization: `Bearer ${context.accessToken}`,
      "X-Request-ID": context.requestId,
      "Cache-Control": "no-store, private",
      Accept: "application/json",
    },
  }), options, "vocabulary:read");
}

export async function validateVocabularyConcept(
  vocabularyUri: string,
  uri: string,
  options: CatalogRequestOptions = {},
): Promise<ConceptValidation> {
  const id = await vocabularyId(vocabularyUri, options);
  return executeCatalogRequest(async (client, context) => client.POST("/api/v1/vocabularies/{vocabulary_id}/validate", {
    params: { path: { vocabulary_id: id } },
    body: { uri },
    headers: {
      Authorization: `Bearer ${context.accessToken}`,
      "X-Request-ID": context.requestId,
      "Cache-Control": "no-store, private",
      Accept: "application/json",
    },
  }), options, "vocabulary:read");
}

export async function resolveVocabularyConcept(
  vocabularyUri: string,
  uri: string,
  locale: string,
  options: CatalogRequestOptions = {},
): Promise<{ concept?: VocabularyConcept; validation?: ConceptValidation }> {
  const page = await searchVocabularyConcepts(vocabularyUri, { uri, locale, limit: 1 }, options);
  if (page.items[0]?.uri === uri) return { concept: page.items[0] };
  return { validation: await validateVocabularyConcept(vocabularyUri, uri, options) };
}
