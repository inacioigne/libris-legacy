"use server";

import type { AuthorityPage, AuthorityType, Link, RelationCollection, RelationEnvelope } from "../../catalog/authorities";
import { messages, type Locale } from "../../i18n/messages";
import { CatalogAccessError, requireCatalogMutationOrigin } from "./index";
import { changeAuthorityRelation, getAuthorityRelations, listAuthorities, type AuthorityQuery } from "./authorities";
import { instanceErrorFeedback } from "./problems";

export type AuthorityResult = { ok: boolean; status?: number; message?: string; issues?: string[]; requestId?: string; page?: AuthorityPage; envelope?: RelationEnvelope };
function failure(error: unknown, locale: Locale): AuthorityResult {
  const status = error instanceof CatalogAccessError ? error.status : 503;
  const feedback = instanceErrorFeedback(status, error instanceof CatalogAccessError ? error.detail : undefined, locale);
  return { ok: false, ...feedback, message: status === 404 ? messages[locale].authorityNotFound : status === 422 ? messages[locale].authorityInvalid : feedback.message };
}
function validCollection(value: string): value is RelationCollection { return ["works", "instances", "items"].includes(value); }
export async function searchAuthoritiesAction(type: AuthorityType, query: AuthorityQuery, locale: Locale): Promise<AuthorityResult> {
  const safeLocale = locale === "en" ? "en" : "pt";
  try {
    if (type !== "Agent" && type !== "Subject") throw new CatalogAccessError(422, "validation_error");
    return { ok: true, page: await listAuthorities(type, query) };
  } catch (error) { return failure(error, safeLocale); }
}
export async function reloadAuthorityRelationsAction(collection: RelationCollection, id: string, locale: Locale): Promise<AuthorityResult> {
  try {
    if (!validCollection(collection)) throw new CatalogAccessError(422, "validation_error");
    return { ok: true, envelope: await getAuthorityRelations(collection, id) };
  } catch (error) { return failure(error, locale === "en" ? "en" : "pt"); }
}
export async function changeAuthorityRelationAction(collection: RelationCollection, id: string, op: "add" | "remove", link: Link, ifMatch: string, locale: Locale): Promise<AuthorityResult> {
  const safeLocale = locale === "en" ? "en" : "pt";
  try {
    await requireCatalogMutationOrigin();
    if (!validCollection(collection) || (op !== "add" && op !== "remove")) throw new CatalogAccessError(422, "validation_error");
    return { ok: true, message: messages[safeLocale].authoritySaved, envelope: await changeAuthorityRelation(collection, id, op, link, { ifMatch }) };
  } catch (error) { return failure(error, safeLocale); }
}
