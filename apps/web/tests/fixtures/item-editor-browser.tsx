import React from "react";
import { richInstance } from "../instance-editor-fixture";
import { ItemDescription } from "../../src/components/catalog/ItemViews";
import { createRoot } from "react-dom/client";
import { ItemEditorForm } from "../../src/components/catalog/ItemEditorForm";
import { richItem } from "../item-editor-fixture";
import type { ItemFormState } from "../../src/server/catalog/item-actions";
import { itemErrorFeedback } from "../../src/server/catalog/problems";
import type { ItemUpdate } from "../../src/catalog/item-editor";

declare global {
  interface Window {
    itemSubmissions: { etag: string | null; payload: ItemUpdate }[];
    nextItemStatus: number;
    currentItem: typeof richItem;
    itemETag: string;
    itemConfirms: number;
    itemConfirmResult: boolean;
  }
}
window.itemSubmissions = [];
window.nextItemStatus = 412;
window.currentItem = structuredClone(richItem);
window.currentItem.custodial_history![0].value = "Remote custody";
window.currentItem.note = [{ label: [{ value: "Remote note", language: "en" }] }];
window.itemETag = '"current"';
window.itemConfirms = 0;
window.itemConfirmResult = false;
window.confirm = () => { window.itemConfirms++; return window.itemConfirmResult; };
const locale = new URLSearchParams(location.search).get("locale") === "pt" ? "pt" : "en";
const create = new URLSearchParams(location.search).has("create");
const action = async (_previous: ItemFormState | undefined, data: FormData): Promise<ItemFormState> => {
  await new Promise(resolve => setTimeout(resolve, 250));
  const payload: ItemUpdate = JSON.parse(String(data.get("payload")));
  window.itemSubmissions.push({ etag: data.get("ifMatch") as string | null, payload });
  if (window.nextItemStatus !== 200) return { ok: false, ...itemErrorFeedback(window.nextItemStatus, {
    request_id: "00000000-0000-4000-8000-000000000001",
    issues: window.nextItemStatus === 422 ? [{ message: "Invalid URI", field_ids: ["/identified_by/0/value"] }] : [],
  }, locale) };
  if (!create && data.get("ifMatch") !== window.itemETag) return { ok: false, ...itemErrorFeedback(412, {}, locale) };
  window.currentItem = { ...window.currentItem, ...payload } as typeof richItem;
  window.itemETag = '"saved"';
  return { ok: true, message: "Saved", saved: { item: window.currentItem, etag: window.itemETag } };
};
createRoot(document.getElementById("root")!).render(<><ItemDescription locale={locale} envelope={{ item: richItem, etag: "test" }} /><ItemEditorForm locale={locale} mode={create ? "create" : "edit"}
  initialInstance={richInstance}
  action={action} item={create ? undefined : richItem} itemId={create ? undefined : richItem.internal_id}
  ifMatch={create ? undefined : '\"original\"'} cancelHref="/cancel"
  validate={async () => ({ ok: true, message: locale === "pt" ? "Pré-validação concluída" : "Prevalidation completed" })}
  reload={async () => ({ ok: true, remote: { item: window.currentItem, etag: window.itemETag } })} /></>);
