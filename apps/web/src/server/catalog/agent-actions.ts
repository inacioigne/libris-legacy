"use server";

import { parseAgentPayload, type AgentRead, type AgentUpdate } from "../../catalog/agent-editor";
import { messages } from "../../i18n/messages";
import { CatalogAccessError, requireCatalogMutationOrigin, requireCatalogPermission } from "./index";
import { createAgent, getAgentForEditor, updateAgent, type AgentEnvelope } from "./authorities";
import { workErrorFeedback } from "./problems";

export type AgentFormState = {
  ok: boolean; message?: string; status?: number; issues?: string[];
  requestId?: string; saved?: AgentRead; envelope?: AgentEnvelope; remote?: AgentEnvelope;
};

function failure(error: unknown, locale: "pt" | "en"): AgentFormState {
  const status = error instanceof CatalogAccessError ? error.status : 503;
  const feedback = workErrorFeedback(status, error instanceof CatalogAccessError ? error.detail : undefined, locale);
  return { ok: false, status, issues: feedback.issues, requestId: feedback.requestId,
    message: status === 422 ? messages[locale].agentFormInvalid : feedback.message };
}

export async function createAgentAction(_previous: AgentFormState, data: FormData): Promise<AgentFormState> {
  const locale = data.get("locale") === "en" ? "en" : "pt";
  try {
    await requireCatalogMutationOrigin();
    await requireCatalogPermission("authority:manage");
    let payload;
    try { payload = parseAgentPayload(data.get("payload")); }
    catch { return { ok: false, status: 422, message: messages[locale].agentFormInvalid }; }
    const saved = await createAgent(payload);
    return { ok: true, saved, message: messages[locale].agentFormSaved };
  } catch (error) {
    return failure(error, locale);
  }
}

export async function updateAgentAction(_previous: AgentFormState, data: FormData): Promise<AgentFormState> {
  const locale = data.get("locale") === "en" ? "en" : "pt";
  try {
    await requireCatalogMutationOrigin();
    await requireCatalogPermission("authority:manage");
    const id = typeof data.get("id") === "string" ? String(data.get("id")) : "";
    const ifMatch = typeof data.get("ifMatch") === "string" ? String(data.get("ifMatch")) : "";
    if (!id || !ifMatch) return { ok: false, status: 428, message: messages[locale].workEditorMissingVersion };
    let complete;
    try { complete = parseAgentPayload(data.get("payload")); }
    catch { return { ok: false, status: 422, message: messages[locale].agentFormInvalid }; }
    const payload: AgentUpdate = {
      authorized_name: complete.authorized_name,
      variant_name: complete.variant_name,
      identified_by: complete.identified_by,
      external_reference: complete.external_reference,
      provenance: complete.provenance,
      related_authority: complete.related_authority,
    };
    const envelope = await updateAgent(id, payload, { ifMatch });
    return { ok: true, envelope, message: messages[locale].agentFormUpdated };
  } catch (error) { return failure(error, locale); }
}

export async function reloadAgentAction(id: string, locale: "pt" | "en"): Promise<AgentFormState> {
  try { return { ok: true, remote: await getAgentForEditor(id) }; }
  catch (error) { return failure(error, locale); }
}
