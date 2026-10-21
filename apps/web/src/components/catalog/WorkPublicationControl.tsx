"use client";

import { useState } from "react";
import { messages, type Locale } from "../../i18n/messages";
import type { EditorialDashboard } from "../../server/catalog/editorial";
import { publishWorkspaceAction } from "../../server/catalog/editorial-actions";

type Props = {
  id: string;
  locale: Locale;
  permissions: string[];
  initial: EditorialDashboard;
};

export function WorkPublicationControl({ id, locale, permissions, initial }: Props) {
  const t = messages[locale];
  const [dashboard, setDashboard] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();
  const state = dashboard.state.candidate_state ?? dashboard.state.editorial_state;
  const draft = state === "draft";
  const canPublish = draft && permissions.includes("bibliographic:update");

  async function publish() {
    if (!dashboard.etag) {
      setFeedback(t.editorialMissingVersion);
      return;
    }
    if (!window.confirm(t.workspacePublishConfirm)) return;
    setBusy(true);
    setFeedback(undefined);
    const result = await publishWorkspaceAction(
      "works",
      id,
      permissions,
      locale,
      dashboard.etag,
    );
    if (result.dashboard) setDashboard(result.dashboard);
    setFeedback(result.message);
    setBusy(false);
  }

  const label = state ? t[`workEditorState_${state}`] : t.workEditorUnknown;
  const published = state === "published";

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`inline-flex rounded-full px-3 py-1 text-sm font-semibold ${
            published
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
              : "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200"
          }`}
        >
          {label}
        </span>
        {canPublish && (
          <button
            type="button"
            onClick={publish}
            disabled={busy}
            className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {busy ? t.workspacePublishing : t.workspacePublish}
          </button>
        )}
      </div>
      {feedback && <p role="status" className="max-w-xl text-sm">{feedback}</p>}
    </div>
  );
}
