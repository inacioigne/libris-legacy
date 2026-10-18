"use client";

import { useState } from "react";
import { messages, type Locale } from "../../i18n/messages";
import type { CatalogCollection, EditorialDashboard, EditorialAction, SemanticDiff } from "../../server/catalog/editorial";
import { compareRevisionsAction, editorialTransitionAction, refreshEditorialAction } from "../../server/catalog/editorial-actions";

type Props = { collection: CatalogCollection; id: string; locale: Locale; permissions: string[]; initial: EditorialDashboard };

const permission: Record<EditorialAction, string> = {
  submit: "editorial:submit", return: "editorial:return", approve: "editorial:approve",
  publish: "editorial:publish", withdraw: "editorial:withdraw", republish: "editorial:republish", archive: "editorial:archive",
};
const requiresReason = new Set<EditorialAction>(["return", "withdraw", "archive"]);
function statement(value: unknown) { return JSON.stringify(value, null, 2); }

function permitted(state: EditorialDashboard["state"], action: EditorialAction): boolean {
  const current = state.candidate_state ?? state.editorial_state;
  if (action === "submit") return current === "draft";
  if (action === "approve") return current === "in_review";
  if (action === "publish") return current === "approved";
  if (action === "return") return current === "in_review" || current === "approved";
  if (action === "withdraw") return state.editorial_state === "published";
  if (action === "republish") return state.editorial_state === "withdrawn";
  return state.editorial_state === "withdrawn";
}

export function EditorialPanel({ collection, id, locale, permissions, initial }: Props) {
  const t = messages[locale];
  const [dashboard, setDashboard] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();
  const [reason, setReason] = useState("");
  const [diff, setDiff] = useState<SemanticDiff>();
  const [compareTo, setCompareTo] = useState<number | undefined>();
  const state = dashboard.state;
  const currentRevision = state.candidate_revision ?? state.current_revision;
  const scope = state.candidate_revision === currentRevision && state.public_revision !== currentRevision ? "candidate" : "record";
  const actions = (Object.keys(permission) as EditorialAction[]).filter(action => permissions.includes(permission[action]) && permitted(state, action));

  async function refresh() {
    setBusy(true); setFeedback(undefined);
    const result = await refreshEditorialAction(collection, id, permissions, locale);
    if (result.dashboard) setDashboard(result.dashboard);
    setFeedback(result.message);
    setBusy(false);
  }
  async function transition(action: EditorialAction) {
    if (requiresReason.has(action) && !reason.trim()) { setFeedback(t.editorialReasonRequired); return; }
    if (!window.confirm(t.editorialConfirm)) return;
    setBusy(true); setFeedback(undefined);
    const result = await editorialTransitionAction(collection, id, action, currentRevision, scope, reason, permissions, locale, dashboard.etag ?? "");
    if (result.dashboard) { setDashboard(result.dashboard); setReason(""); }
    setFeedback(result.message);
    setBusy(false);
  }
  async function compare() {
    if (!compareTo || compareTo === currentRevision) return;
    setBusy(true); setFeedback(undefined);
    const result = await compareRevisionsAction(collection, id, compareTo, currentRevision, locale);
    if (result.diff) setDiff(result.diff);
    setFeedback(result.message);
    setBusy(false);
  }

  return <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900" aria-busy={busy}>
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">{t.editorial}</h2>
      <button type="button" className="rounded-lg border px-3 py-2" onClick={refresh} disabled={busy}>{t.editorialRefresh}</button></div>
    <dl className="grid gap-3 text-sm sm:grid-cols-2">
      <div><dt>{t.workState}</dt><dd>{state.editorial_state ? t[`workEditorState_${state.editorial_state}`] : t.workEditorUnknown}</dd></div>
      <div><dt>{t.editorialCurrentRevision}</dt><dd>{state.current_revision}</dd></div>
      <div><dt>{t.workEditorCandidate}</dt><dd>{state.candidate_revision ? `${state.candidate_revision} — ${state.candidate_state ? t[`workEditorState_${state.candidate_state}`] : t.workEditorUnknown}` : "—"}</dd></div>
      <div><dt>{t.editorialApprovedRevision}</dt><dd>{state.approved_revision ?? "—"}</dd></div>
      <div><dt>{t.editorialPublishedRevision}</dt><dd>{state.published_revision ?? "—"}</dd></div>
      <div><dt>{t.editorialPublicRevision}</dt><dd>{state.public_revision ?? "—"}</dd></div>
    </dl>
    {feedback && <p role="status" className="rounded-lg border p-3">{feedback}</p>}

    {actions.length > 0 && <div className="space-y-3 border-t pt-4"><h3 className="font-semibold">{t.editorialActions}</h3>
      {actions.some(action => requiresReason.has(action)) && <label className="block text-sm">{t.editorialReason}<textarea value={reason} onChange={event => setReason(event.target.value)} className="mt-1 block w-full rounded border p-2" rows={2} /></label>}
      <div className="flex flex-wrap gap-2">{actions.map(action => <button key={action} type="button" disabled={busy} onClick={() => transition(action)} className="rounded-lg bg-teal-700 px-3 py-2 text-white disabled:opacity-50">{t[`editorialAction_${action}`]}</button>)}</div>
      <p className="text-sm">{t.editorialActionHelp}</p></div>}

    {dashboard.revisions && <div className="space-y-3 border-t pt-4"><h3 className="font-semibold">{t.editorialHistory}</h3>
      {dashboard.revisions.items.length === 0 ? <p>{t.editorialEmptyHistory}</p> : <><ul className="space-y-2">{dashboard.revisions.items.map(({ revision }) => <li key={revision.number} className="rounded border p-2"><strong>R{revision.number}</strong> · {revision.created_at} · {revision.profile}{revision.actor_id ? ` · ${revision.actor_id}` : ""}</li>)}</ul>
      <div className="flex flex-wrap gap-2"><label>{t.editorialCompareWith}<select className="ml-2 rounded border p-1" value={compareTo ?? ""} onChange={event => setCompareTo(Number(event.target.value) || undefined)}><option value="">—</option>{dashboard.revisions.items.filter(({ revision }) => revision.number !== currentRevision).map(({ revision }) => <option key={revision.number} value={revision.number}>R{revision.number}</option>)}</select></label><button type="button" className="rounded border px-3 py-1" disabled={busy || !compareTo} onClick={compare}>{t.editorialCompare}</button></div></>}</div>}
    {diff && <div className="space-y-3 border-t pt-4"><h3 className="font-semibold">{t.editorialDifferences}</h3><div className="grid gap-3 md:grid-cols-2"><div><h4>{t.editorialAdded}</h4><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{statement(diff.added)}</pre></div><div><h4>{t.editorialRemoved}</h4><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{statement(diff.removed)}</pre></div></div></div>}
    {dashboard.audit && <div className="space-y-3 border-t pt-4"><h3 className="font-semibold">{t.editorialAudit}</h3>{dashboard.audit.items.length === 0 ? <p>{t.editorialEmptyAudit}</p> : <ul className="space-y-2">{dashboard.audit.items.map(event => <li key={event.event_id} className="rounded border p-2 text-sm"><strong>{event.event_type}</strong> · {event.occurred_at} · R{event.revision_number}{event.actor_id ? ` · ${event.actor_id}` : ""}{event.reason ? ` · ${event.reason}` : ""}</li>)}</ul>}</div>}
    {!dashboard.revisions && !dashboard.audit && <p className="text-sm">{t.editorialRestricted}</p>}
  </section>;
}
