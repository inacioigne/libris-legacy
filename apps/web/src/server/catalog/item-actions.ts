"use server";

import { messages, type Locale } from "../../i18n/messages";
import { parseItemPayload, type ItemCreate, type ItemUpdate } from "../../catalog/item-editor";
import { CatalogAccessError, requireCatalogMutationOrigin } from "./index";
import { createItem, enrichItem, getItemForEditor, updateItem, validateItem, type ItemEnvelope } from "./items";
import { getInstance } from "./instances";
import { itemErrorFeedback } from "./problems";

export type ItemFormState = {
  ok: boolean; message?: string; status?: number; redirectTo?: string; issues?: string[];
  fieldErrors?: Record<string, string>; requestId?: string; saved?: ItemEnvelope;
};
export type ItemReloadState = ItemFormState & { remote?: ItemEnvelope };
function localeOf(data: FormData): Locale { return data.get("locale") === "en" ? "en" : "pt"; }
function failure(error: unknown, locale: Locale): ItemFormState {
  return { ok: false, ...itemErrorFeedback(error instanceof CatalogAccessError ? error.status : 503, error instanceof CatalogAccessError ? error.detail : undefined, locale) };
}
async function checkSelectedInstance(payload: ItemCreate | { item_of?: ItemCreate["item_of"] | null }) {
  const reference = payload.item_of;
  if (!reference) return;
  if (!("kind" in reference) || reference.kind !== "local" || !reference.internal_id) throw new CatalogAccessError(422, "validation_error");
  const { instance } = await getInstance(reference.internal_id);
  if (instance.uri !== reference.uri) throw new CatalogAccessError(422, "validation_error");
}
export async function createItemAction(_previous: ItemFormState | undefined, data: FormData): Promise<ItemFormState> {
  const locale = localeOf(data);
  try {
    await requireCatalogMutationOrigin();
    let payload: ItemCreate;
    try { payload = parseItemPayload(data.get("payload"), "create") as ItemCreate; }
    catch { return { ok: false, status: 422, message: messages[locale].itemValidationError }; }
    await checkSelectedInstance(payload);
    const saved = await createItem(payload);
    return { ok: true, message: messages[locale].itemSaved, redirectTo: `/${locale}/admin/catalog/items/${saved.internal_id}?saved=1` };
  } catch (error) { return failure(error, locale); }
}
export async function updateItemAction(_previous: ItemFormState | undefined, data: FormData): Promise<ItemFormState> {
  const locale = localeOf(data);
  try {
    await requireCatalogMutationOrigin();
    const id = String(data.get("id") ?? ""), ifMatch = String(data.get("ifMatch") ?? "");
    if (!id || !ifMatch) throw new CatalogAccessError(428, "precondition_failed");
    let payload;
    try { payload = parseItemPayload(data.get("payload"), "edit") as ItemUpdate; }
    catch { return { ok: false, status: 422, message: messages[locale].itemValidationError }; }
    const saved = await updateItem(id, payload, { ifMatch });
    return { ok: true, message: messages[locale].itemSaved, saved: await enrichItem(saved) };
  } catch (error) { return failure(error, locale); }
}
export async function reloadItemAction(id: string, locale: Locale): Promise<ItemReloadState> {
  const safeLocale = locale === "en" ? "en" : "pt";
  try { return { ok: true, remote: await getItemForEditor(id) }; }
  catch (error) { return failure(error, safeLocale); }
}
export async function validateItemAction(data: FormData): Promise<ItemFormState> {
  const locale = localeOf(data);
  try {
    await requireCatalogMutationOrigin();
    let payload: ItemCreate;
    try { payload = parseItemPayload(data.get("payload"), "create") as ItemCreate; }
    catch { return { ok: false, status: 422, message: messages[locale].itemValidationError }; }
    await checkSelectedInstance(payload);
    const result = await validateItem(payload);
    const feedback = itemErrorFeedback(422, { validation: result }, locale);
    return { ...feedback, ok: result.conforms === true, message: result.conforms === true ? messages[locale].itemValid : feedback.message };
  } catch (error) { return failure(error, locale); }
}
