"use server";

import { messages, type Locale } from "../../i18n/messages";
import { parseInstancePayload, type InstanceCreate, type InstanceUpdate } from "../../catalog/instance-editor";
import { CatalogAccessError, getWork, listWorks, requireCatalogMutationOrigin, type WorkListPage } from "./index";
import { createInstance, enrichInstance, getInstanceForEditor, updateInstance, validateInstance, type InstanceEnvelope } from "./instances";
import { instanceErrorFeedback } from "./problems";

export type InstanceFormState = {
  ok: boolean; message?: string; status?: number; redirectTo?: string; issues?: string[];
  fieldErrors?: Record<string, string>; requestId?: string; saved?: InstanceEnvelope;
};
export type InstanceReloadState = InstanceFormState & { remote?: InstanceEnvelope };
export type WorkSelectionState = InstanceFormState & { page?: WorkListPage };
function localeOf(data: FormData): Locale { return data.get("locale") === "en" ? "en" : "pt"; }
function failure(error: unknown, locale: Locale): InstanceFormState {
  return { ok: false, ...instanceErrorFeedback(error instanceof CatalogAccessError ? error.status : 503, error instanceof CatalogAccessError ? error.detail : undefined, locale) };
}
async function checkSelectedWork(payload: InstanceCreate | { instance_of?: InstanceCreate["instance_of"] | null }) {
  const reference = payload.instance_of;
  if (!reference) return;
  if (!("kind" in reference) || reference.kind !== "local" || !reference.internal_id) throw new CatalogAccessError(422, "validation_error");
  const work = await getWork(reference.internal_id);
  if (work.uri !== reference.uri) throw new CatalogAccessError(422, "validation_error");
}
export async function createInstanceAction(_previous: InstanceFormState | undefined, data: FormData): Promise<InstanceFormState> {
  const locale = localeOf(data);
  try {
    await requireCatalogMutationOrigin();
    let payload: InstanceCreate;
    try { payload = parseInstancePayload(data.get("payload"), "create") as InstanceCreate; }
    catch { return { ok: false, status: 422, message: messages[locale].instanceValidationError }; }
    await checkSelectedWork(payload);
    const saved = await createInstance(payload);
    return { ok: true, message: messages[locale].instanceSaved, redirectTo: `/${locale}/admin/catalog/instances/${saved.internal_id}?saved=1` };
  } catch (error) { return failure(error, locale); }
}
export async function updateInstanceAction(_previous: InstanceFormState | undefined, data: FormData): Promise<InstanceFormState> {
  const locale = localeOf(data);
  try {
    await requireCatalogMutationOrigin();
    const id = String(data.get("id") ?? ""), ifMatch = String(data.get("ifMatch") ?? "");
    if (!id || !ifMatch) throw new CatalogAccessError(428, "precondition_failed");
    let payload;
    try { payload = parseInstancePayload(data.get("payload"), "edit") as InstanceUpdate; }
    catch { return { ok: false, status: 422, message: messages[locale].instanceValidationError }; }
    await checkSelectedWork(payload);
    const saved = await updateInstance(id, payload, { ifMatch });
    return { ok: true, message: messages[locale].instanceSaved, saved: await enrichInstance(saved) };
  } catch (error) { return failure(error, locale); }
}
export async function reloadInstanceAction(id: string, locale: Locale): Promise<InstanceReloadState> {
  const safeLocale = locale === "en" ? "en" : "pt";
  try { return { ok: true, remote: await getInstanceForEditor(id) }; }
  catch (error) { return failure(error, safeLocale); }
}
export async function selectWorksAction(query: { internal_id?: string; uri?: string; cursor?: string }, locale: Locale): Promise<WorkSelectionState> {
  const safeLocale = locale === "en" ? "en" : "pt";
  try { return { ok: true, page: await listWorks({ internal_id: query.internal_id, uri: query.uri, cursor: query.cursor, limit: 10 }) }; }
  catch (error) { return failure(error, safeLocale); }
}
export async function validateInstanceAction(data: FormData): Promise<InstanceFormState> {
  const locale = localeOf(data);
  try {
    await requireCatalogMutationOrigin();
    let payload: InstanceCreate;
    try { payload = parseInstancePayload(data.get("payload"), "create") as InstanceCreate; }
    catch { return { ok: false, status: 422, message: messages[locale].instanceValidationError }; }
    await checkSelectedWork(payload);
    const result = await validateInstance(payload);
    const feedback = instanceErrorFeedback(422, { validation: result }, locale);
    return { ...feedback, ok: result.conforms === true, message: result.conforms === true ? messages[locale].instanceValid : feedback.message };
  } catch (error) { return failure(error, locale); }
}
