"use server";

import { messages, type Locale } from "../../i18n/messages";
import { CatalogAccessError } from "./index";
import { getEditorialDashboard, getRevisionDiff, transitionEditorial, type CatalogCollection, type EditorialAction } from "./editorial";

export type EditorialResult = { ok: boolean; status?: number; message?: string; dashboard?: Awaited<ReturnType<typeof getEditorialDashboard>>; diff?: Awaited<ReturnType<typeof getRevisionDiff>> };

function failure(error: unknown, locale: Locale): EditorialResult {
  const status = error instanceof CatalogAccessError ? error.status : 503;
  const message = status === 401 ? messages[locale].editorialUnauthenticated
    : status === 403 ? messages[locale].editorialForbidden
    : status === 409 ? messages[locale].editorialConflict
    : status === 412 ? messages[locale].editorialStale
    : status === 422 ? messages[locale].editorialInvalid
    : messages[locale].editorialUnavailable;
  return { ok: false, status, message };
}

export async function refreshEditorialAction(collection: CatalogCollection, id: string, permissions: string[], locale: Locale): Promise<EditorialResult> {
  try { return { ok: true, dashboard: await getEditorialDashboard(collection, id, permissions) }; }
  catch (error) { return failure(error, locale); }
}

export async function compareRevisionsAction(collection: CatalogCollection, id: string, previous: number, current: number, locale: Locale): Promise<EditorialResult> {
  try { return { ok: true, diff: await getRevisionDiff(collection, id, previous, current) }; }
  catch (error) { return failure(error, locale); }
}

export async function editorialTransitionAction(collection: CatalogCollection, id: string, action: EditorialAction, selectedRevision: number, scope: "record" | "candidate", justification: string, permissions: string[], locale: Locale, ifMatch: string): Promise<EditorialResult> {
  try {
    await transitionEditorial(collection, id, action, { selected_revision: selectedRevision, scope, justification: justification || null }, ifMatch);
    return { ok: true, message: messages[locale].editorialTransitionSuccess, dashboard: await getEditorialDashboard(collection, id, permissions) };
  } catch (error) { return failure(error, locale); }
}
