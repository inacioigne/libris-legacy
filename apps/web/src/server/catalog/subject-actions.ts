"use server";

import { parseSubjectPayload, type SubjectUpdate } from "../../catalog/subject-editor";
import { messages } from "../../i18n/messages";
import { CatalogAccessError, requireCatalogMutationOrigin, requireCatalogPermission } from "./index";
import { createSubject, getSubjectForEditor, updateSubject, type SubjectEnvelope } from "./authorities";
import { workErrorFeedback } from "./problems";

export type SubjectFormState = { ok: boolean; status?: number; message?: string; issues?: string[]; requestId?: string; saved?: SubjectEnvelope["subject"]; envelope?: SubjectEnvelope; remote?: SubjectEnvelope };
function failure(error: unknown, locale: "pt" | "en"): SubjectFormState { const status = error instanceof CatalogAccessError ? error.status : 503; const feedback = workErrorFeedback(status, error instanceof CatalogAccessError ? error.detail : undefined, locale); return { ok: false, status, issues: feedback.issues, requestId: feedback.requestId, message: status === 422 ? messages[locale].subjectFormInvalid : feedback.message }; }
export async function createSubjectAction(_previous: SubjectFormState, data: FormData): Promise<SubjectFormState> { const locale = data.get("locale") === "en" ? "en" : "pt"; try { await requireCatalogMutationOrigin(); await requireCatalogPermission("authority:manage"); const payload = parseSubjectPayload(data.get("payload")); return { ok: true, saved: await createSubject(payload), message: messages[locale].subjectFormSaved }; } catch (error) { return failure(error, locale); } }
export async function updateSubjectAction(_previous: SubjectFormState, data: FormData): Promise<SubjectFormState> { const locale = data.get("locale") === "en" ? "en" : "pt"; try { await requireCatalogMutationOrigin(); await requireCatalogPermission("authority:manage"); const id = String(data.get("id") ?? ""), ifMatch = String(data.get("ifMatch") ?? ""); if (!id || !ifMatch) return { ok: false, status: 428, message: messages[locale].workEditorMissingVersion }; const complete = parseSubjectPayload(data.get("payload")); const payload: SubjectUpdate = complete; return { ok: true, envelope: await updateSubject(id, payload, { ifMatch }), message: messages[locale].subjectFormUpdated }; } catch (error) { return failure(error, locale); } }
export async function reloadSubjectAction(id: string, locale: "pt" | "en"): Promise<SubjectFormState> { try { return { ok: true, remote: await getSubjectForEditor(id) }; } catch (error) { return failure(error, locale); } }
