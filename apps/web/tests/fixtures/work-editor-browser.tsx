import React from "react";
import { createRoot } from "react-dom/client";
import { WorkEditorForm } from "../../src/components/catalog/WorkEditorForm";
import { richWork } from "../work-editor-fixture";
import type { WorkFormState } from "../../src/server/catalog/actions";
import { workErrorFeedback } from "../../src/server/catalog/problems";
import type { WorkUpdate } from "../../src/catalog/work-editor";

declare global {
  interface Window {
    submissions: { etag: string | null; payload: WorkUpdate }[];
    nextStatus: number;
    currentWork: typeof richWork;
    currentETag: string;
    confirms: number;
    confirmResult: boolean;
  }
}
window.submissions = [];
window.nextStatus = 412;
window.currentWork = structuredClone(richWork);
window.currentWork.title[0].main_title[0].value = "Remote title";
window.currentWork.note = [{ label: [{ value: "Remote note", language: "en" }] }];
window.currentETag = '"current"';
window.confirms = 0;
window.confirmResult = false;
window.confirm = () => { window.confirms++; return window.confirmResult; };
const locale = new URLSearchParams(location.search).get("locale") === "pt" ? "pt" : "en";
const create = new URLSearchParams(location.search).has("create");
const action = async (_previous: WorkFormState | undefined, data: FormData): Promise<WorkFormState> => {
  const payload: WorkUpdate = JSON.parse(String(data.get("payload")));
  window.submissions.push({ etag: data.get("ifMatch") as string | null, payload });
  if (window.nextStatus !== 200) return { ok: false, ...workErrorFeedback(window.nextStatus, {
    request_id: "00000000-0000-4000-8000-000000000001",
    issues: window.nextStatus === 422 ? [{ message: "Invalid URI", field_ids: ["/language/0/uri"] }] : [],
  }, locale) };
  if (!create && data.get("ifMatch") !== window.currentETag) return { ok: false, ...workErrorFeedback(412, {}, locale) };
  window.currentWork = { ...window.currentWork, ...payload } as typeof richWork;
  window.currentETag = '"saved"';
  return { ok: true, message: "Saved", saved: { work: window.currentWork, etag: window.currentETag } };
};
createRoot(document.getElementById("root")!).render(<WorkEditorForm locale={locale} mode={create ? "create" : "edit"}
  action={action} work={create ? undefined : richWork} workId={create ? undefined : richWork.internal_id}
  ifMatch={create ? undefined : '"original"'} cancelHref="/cancel"
  reload={async () => ({ ok: true, remote: { work: window.currentWork, etag: window.currentETag } })} />);
