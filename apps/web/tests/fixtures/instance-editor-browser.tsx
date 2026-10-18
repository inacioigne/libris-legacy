import React from "react";
import { richWork } from "../work-editor-fixture";
import { InstanceList, InstanceDescription } from "../../src/components/catalog/InstanceViews";
import { createRoot } from "react-dom/client";
import { InstanceEditorForm } from "../../src/components/catalog/InstanceEditorForm";
import { richInstance } from "../instance-editor-fixture";
import type { InstanceFormState } from "../../src/server/catalog/instance-actions";
import { instanceErrorFeedback } from "../../src/server/catalog/problems";
import type { InstanceUpdate } from "../../src/catalog/instance-editor";

declare global {
  interface Window {
    instanceSubmissions: { etag: string | null; payload: InstanceUpdate }[];
    nextInstanceStatus: number;
    currentInstance: typeof richInstance;
    instanceETag: string;
    instanceConfirms: number;
    instanceConfirmResult: boolean;
  }
}
window.instanceSubmissions = [];
window.nextInstanceStatus = 412;
window.currentInstance = structuredClone(richInstance);
window.currentInstance.title![0].main_title[0].value = "Remote title";
window.currentInstance.note = [{ label: [{ value: "Remote note", language: "en" }] }];
window.instanceETag = '"current"';
window.instanceConfirms = 0;
window.instanceConfirmResult = false;
window.confirm = () => { window.instanceConfirms++; return window.instanceConfirmResult; };
const locale = new URLSearchParams(location.search).get("locale") === "pt" ? "pt" : "en";
const create = new URLSearchParams(location.search).has("create");
const action = async (_previous: InstanceFormState | undefined, data: FormData): Promise<InstanceFormState> => {
  await new Promise(resolve => setTimeout(resolve, 250));
  const payload: InstanceUpdate = JSON.parse(String(data.get("payload")));
  window.instanceSubmissions.push({ etag: data.get("ifMatch") as string | null, payload });
  if (window.nextInstanceStatus !== 200) return { ok: false, ...instanceErrorFeedback(window.nextInstanceStatus, {
    request_id: "00000000-0000-4000-8000-000000000001",
    issues: window.nextInstanceStatus === 422 ? [{ message: "Invalid URI", field_ids: ["/carrier/0/uri"] }] : [],
  }, locale) };
  if (!create && data.get("ifMatch") !== window.instanceETag) return { ok: false, ...instanceErrorFeedback(412, {}, locale) };
  window.currentInstance = { ...window.currentInstance, ...payload } as typeof richInstance;
  window.instanceETag = '"saved"';
  return { ok: true, message: "Saved", saved: { instance: window.currentInstance, etag: window.instanceETag } };
};
createRoot(document.getElementById("root")!).render(<><InstanceList locale={locale} canCreate page={{ order: "internal_id:asc", items: new URLSearchParams(location.search).has("empty") ? [] : [richInstance], next_cursor: null, limit: 20 }} /><InstanceDescription locale={locale} envelope={{ instance: richInstance, etag: "test" }} /><InstanceEditorForm locale={locale} mode={create ? "create" : "edit"}
  searchWorks={async () => ({ ok: true, page: { order: "internal_id:asc", items: [richWork], next_cursor: null, limit: 10 } })}
  action={action} instance={create ? undefined : richInstance} instanceId={create ? undefined : richInstance.internal_id}
  ifMatch={create ? undefined : '"original"'} cancelHref="/cancel"
  reload={async () => ({ ok: true, remote: { instance: window.currentInstance, etag: window.instanceETag } })} /></>);
