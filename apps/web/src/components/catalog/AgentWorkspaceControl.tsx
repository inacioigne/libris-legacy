"use client";

import { useState } from "react";
import { messages, type Locale } from "../../i18n/messages";
import type { EditorialDashboard } from "../../server/catalog/editorial";
import { publishWorkspaceAction, validateWorkspaceAction } from "../../server/catalog/editorial-actions";

type Props = {
  id: string;
  locale: Locale;
  permissions: string[];
  initial: EditorialDashboard;
};

export function AgentWorkspaceControl({ id, locale, permissions, initial }: Props) {
  const t = messages[locale];
  const [dashboard, setDashboard] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();
  const state = dashboard.state.candidate_state ?? dashboard.state.editorial_state;
  const legacy = state === "in_review" || state === "approved";
  const canManage = permissions.includes("authority:manage");

  async function validate() {
    setBusy(true);
    setFeedback(undefined);
    const result = await validateWorkspaceAction("agents", id, locale, dashboard.etag ?? "");
    setFeedback(result.message);
    setBusy(false);
  }

  async function publish() {
    if (!window.confirm(t.workspacePublishConfirm)) return;
    setBusy(true);
    setFeedback(undefined);
    const result = await publishWorkspaceAction("agents", id, permissions, locale, dashboard.etag ?? "");
    if (result.dashboard) setDashboard(result.dashboard);
    setFeedback(result.message);
    setBusy(false);
  }

  return <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900" aria-busy={busy}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-xl font-semibold">{t.workspaceFlowLabel}</h2>
      <span className="rounded-full bg-amber-100 px-3 py-1 text-sm font-semibold text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        {state ? t[`workEditorState_${state}`] : t.workEditorUnknown}
      </span>
    </div>
    <ol className="grid gap-3 sm:grid-cols-3" aria-label={t.workspaceFlowLabel}>
      <li className="rounded-lg border p-3"><strong>1. {t.workspaceSaveDraft}</strong><p className="mt-1 text-sm">{t.workspaceSaveDraftHelp}</p></li>
      <li className="rounded-lg border p-3"><strong>2. {t.workspaceValidate}</strong><p className="mt-1 text-sm">{t.workspaceValidateHelp}</p></li>
      <li className="rounded-lg border p-3"><strong>3. {t.workspacePublish}</strong><p className="mt-1 text-sm">{t.workspacePublishHelp}</p></li>
    </ol>
    {feedback && <p role="status" className="rounded-lg border p-3">{feedback}</p>}
    {legacy ? <p role="note" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">{t.workspaceLegacyHelp}</p>
      : canManage && state === "draft" ? <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy || !dashboard.etag} onClick={validate} className="rounded-lg border border-teal-700 px-3 py-2 text-teal-800 disabled:opacity-50 dark:text-teal-200">{t.workspaceValidate}</button>
        <button type="button" disabled={busy || !dashboard.etag} onClick={publish} className="rounded-lg bg-teal-700 px-3 py-2 text-white disabled:opacity-50">{busy ? t.workspacePublishing : t.workspacePublish}</button>
      </div> : null}
  </section>;
}
