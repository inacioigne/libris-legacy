"use server";

import { messages, type Locale } from "../../i18n/messages";
import { parseEditorPayload, type WorkCreate } from "../../catalog/work-editor";
import { CatalogAccessError, requireCatalogMutationOrigin, createWork, getWorkForEditor, enrichWorkEnvelope, updateWorkWithMetadata, type WorkEnvelope } from "./index";
import { workErrorFeedback } from "./problems";

export type WorkFormState = {
  ok: boolean;
  message?: string;
  status?: number;
  field?: string;
  redirectTo?: string;
  issues?: string[];
  fieldErrors?: Record<string, string>;
  requestId?: string;
  saved?: WorkEnvelope;
};
export type WorkReloadState = WorkFormState & { remote?: WorkEnvelope };

function readString(value: FormDataEntryValue | null): string {
  return typeof value === "string" ? value : "";
}
function localeOf(formData: FormData): Locale {
  return formData.get("locale") === "en" ? "en" : "pt";
}
function failure(error: unknown, locale: Locale): WorkFormState {
  return { ok: false, ...workErrorFeedback(
    error instanceof CatalogAccessError ? error.status : 503,
    error instanceof CatalogAccessError ? error.detail : undefined, locale,
  ) };
}

export async function createWorkAction(_prevState: WorkFormState | undefined, formData: FormData): Promise<WorkFormState> {
  const locale = localeOf(formData);
  try { await requireCatalogMutationOrigin(); }
  catch (error) { return failure(error, locale); }
  let payload: WorkCreate;
  try { payload = parseEditorPayload(formData.get("payload"), "create") as WorkCreate; }
  catch { return { ok: false, status: 422, message: messages[locale].workFormValidationError }; }
  try {
    const created = await createWork(payload);
    return { ok: true, message: messages[locale].workFormSuccess,
      redirectTo: `/${locale}/admin/catalog/works/${created.internal_id}` };
  } catch (error) { return failure(error, locale); }
}

export async function updateWorkAction(_prevState: WorkFormState | undefined, formData: FormData): Promise<WorkFormState> {
  const locale = localeOf(formData);
  try { await requireCatalogMutationOrigin(); }
  catch (error) { return failure(error, locale); }
  const id = readString(formData.get("id"));
  const ifMatch = readString(formData.get("ifMatch"));
  if (!id || !ifMatch) return { ok: false, status: 428, message: messages[locale].workEditorMissingVersion };
  let payload;
  try { payload = parseEditorPayload(formData.get("payload"), "edit"); }
  catch { return { ok: false, status: 422, message: messages[locale].workFormValidationError }; }
  if (Object.keys(payload).length === 0) return { ok: true, message: messages[locale].workEditorUnchanged };
  try {
    const saved = await updateWorkWithMetadata(id, payload, { ifMatch });
    return { ok: true, message: messages[locale].workFormSuccess, saved: await enrichWorkEnvelope(saved) };
  } catch (error) { return failure(error, locale); }
}

// Read through the same authenticated BFF. Loading does not adopt the new ETag.
export async function reloadWorkAction(id: string, locale: Locale): Promise<WorkReloadState> {
  const safeLocale = locale === "en" ? "en" : "pt";
  try { return { ok: true, remote: await getWorkForEditor(id) }; }
  catch (error) { return failure(error, safeLocale); }
}
