"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { messages, type Locale } from "../../i18n/messages";
import { changedFields, createPayload, draftFromWork, reconcileWork, workPatch, type ReconciliationChoices, type WorkRead } from "../../catalog/work-editor";
import type { WorkFormState, WorkReloadState } from "../../server/catalog/actions";
import type { WorkEnvelope } from "../../server/catalog";
import { buttonClass, WorkEditorFields } from "./WorkEditorFields";

type Props = {
  locale: Locale;
  mode: "create" | "edit";
  action: (prevState: WorkFormState | undefined, formData: FormData) => Promise<WorkFormState>;
  reload?: (id: string, locale: Locale) => Promise<WorkReloadState>;
  work?: WorkRead | null;
  workId?: string;
  metadata?: WorkEnvelope["metadata"];
  ifMatch?: string;
  cancelHref: string;
  validate?: (data: FormData) => Promise<WorkFormState>;
};

export function WorkEditorForm({ locale, mode, action, reload, work, workId, ifMatch, metadata, cancelHref, validate }: Props) {
  const t = messages[locale];
  const [state, formAction, pending] = useActionState(action, { ok: true });
  const [base, setBase] = useState(() => draftFromWork(work));
  const [draft, setDraft] = useState(() => draftFromWork(work));
  const [editorial, setEditorial] = useState(metadata);
  const [loaded, setLoaded] = useState(work);
  const [etag, setEtag] = useState(ifMatch);
  const [remote, setRemote] = useState<WorkEnvelope>();
  const [choices, setChoices] = useState<ReconciliationChoices>({});
  const [feedback, setFeedback] = useState<WorkFormState>();
  const [loading, setLoading] = useState(false);
  const [needsComparison, setNeedsComparison] = useState(false);
  const savedNavigation = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const summary = useRef<HTMLDivElement>(null);
  const comparison = useRef<HTMLDivElement>(null);
  const changes = changedFields(base, draft);
  const dirty = changes.length > 0;
  const result = feedback ?? state;
  const busy = pending || loading;
  const labels = { title: t.workEditorTitles, language: t.workFormLanguageLabel, contribution: t.workFormContributorLabel, subject: t.workFormSubjectLabel, note: t.workFormNoteLabel };

  useEffect(() => {
    if (state.saved) {
      setBase(draftFromWork(state.saved.work)); setDraft(draftFromWork(state.saved.work));
      setLoaded(state.saved.work); setEditorial(state.saved.metadata); setEtag(state.saved.etag ?? undefined);
      setRemote(undefined); setNeedsComparison(false);
    }
    if (state.status === 412) setNeedsComparison(true);
    if (state.ok && state.redirectTo) { savedNavigation.current = true; window.location.assign(state.redirectTo); }
  }, [state]);
  useEffect(() => {
    if (!result.ok) (form.current?.querySelector<HTMLElement>('[aria-invalid="true"]') ?? summary.current)?.focus();
  }, [result]);
  useEffect(() => {
    if (remote) comparison.current?.focus();
  }, [remote]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { if (savedNavigation.current) return; event.preventDefault(); event.returnValue = ""; };
    const guardLink = (event: MouseEvent) => {
      const target = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (target && !target.hasAttribute("target") && !window.confirm(t.workEditorDiscard)) {
        event.preventDefault(); event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", warn);
    document.addEventListener("click", guardLink, true);
    return () => { window.removeEventListener("beforeunload", warn); document.removeEventListener("click", guardLink, true); };
  }, [dirty, t.workEditorDiscard]);

  async function loadCurrent() {
    if (!reload || !workId) return;
    setLoading(true);
    try {
      const response = await reload(workId, locale);
      if (response.remote?.etag && response.remote.work.internal_id === workId) {
        setRemote(response.remote); setChoices({}); setFeedback(undefined);
        setNeedsComparison(true);
      } else setFeedback(response.ok ? { ok: false, message: t.workEditorMissingVersion } : response);
    } catch { setFeedback({ ok: false, message: t.workEditorUnavailable }); }
    finally { setLoading(false); }
  }

  return <form ref={form} action={formAction} aria-busy={busy} className="mt-8 space-y-6 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900"
    onInvalidCapture={event => {
      if (event.target instanceof HTMLInputElement) {
        const path = event.target.name;
        setFeedback(current => ({ ok: false, message: t.workFormValidationError, fieldErrors: { ...current?.fieldErrors, [path]: t.workEditorRequired } }));
      }
    }}
    onSubmit={event => {
      if (busy || needsComparison || remote) { event.preventDefault(); return; }
      setFeedback(undefined);
      if (!draft.title.some(title => (title.kind ?? "Title") === "Title" && title.main_title.length > 0)) {
        event.preventDefault(); setFeedback({ ok: false, message: t.workFormRequired });
      }
    }}>
    <input type="hidden" name="locale" value={locale} />
    {workId && <input type="hidden" name="id" value={workId} />}
    {etag && <input type="hidden" name="ifMatch" value={etag} />}
    <input type="hidden" name="payload" value={JSON.stringify(mode === "create" ? createPayload(draft) : workPatch(base, draft))} />
    <p role="status">{busy ? (pending ? t.workFormLoading : t.workEditorLoading) : dirty ? t.workEditorDirty : t.workEditorUnchanged}</p>
    {loaded && <dl className="break-all text-sm"><dt>{t.workIdentifier}</dt><dd>{loaded.internal_id}</dd><dt>{t.workUri}</dt><dd>{loaded.uri}</dd></dl>}
    {loaded && (editorial ? <dl className="break-all text-sm">
      <dt>{t.workState}</dt><dd>{editorial.editorialState ? t[`workEditorState_${editorial.editorialState}`] : t.workEditorUnknown}</dd>
      {editorial.candidateState && <><dt>{t.workEditorCandidate}</dt><dd>{t[`workEditorState_${editorial.candidateState}`]}</dd></>}
      <dt>{t.workEditorRevision}</dt><dd>{editorial.revision}</dd><dt>{t.workEditorProfile}</dt><dd>{editorial.profile}</dd>
    </dl> : <p>{t.workEditorMetadataUnavailable}</p>)}
    <p className="text-sm">{t.workEditorPreservation}</p>
    {result.message && <div ref={summary} tabIndex={-1} role={result.ok ? "status" : "alert"} className="rounded-lg border border-slate-400 p-3">
      <p>{result.message}</p>
      {result.issues?.length ? <ul className="list-disc pl-5">{result.issues.map(issue => <li key={issue}>{issue}</li>)}</ul> : null}
      {result.requestId && <p>{t.workFormRequestId}: {result.requestId}</p>}
      {result.status === 401 && <a href={`/${locale}/admin`} target="_blank" rel="noopener noreferrer" className="underline">{t.workEditorSignIn}</a>}
      {result.status === 403 && <p>{t.workEditorPermissionHelp}</p>}
      {result.status === 409 && <a href={cancelHref} target="_blank" rel="noopener noreferrer" className="underline">{t.workEditorInspect}</a>}
    </div>}
    {mode === "edit" && reload && <button type="button" disabled={busy} className={buttonClass} onClick={loadCurrent}>{t.workEditorLoadCurrent}</button>}
    {remote && <div ref={comparison} tabIndex={-1} className="space-y-4 rounded-lg border-2 border-amber-600 p-4" aria-label={t.workEditorComparison}>
      <h2 className="text-lg font-semibold">{t.workEditorComparison}</h2><p>{t.workEditorCompareHelp}</p>
      {changes.map(field => <fieldset key={field} className="min-w-0 border border-slate-400 p-3">
        <legend>{labels[field]}</legend>
        <div className="grid gap-3 md:grid-cols-2">{(["local", "remote"] as const).map(side => <div key={side}>
          <label><input type="radio" name={`choice-${field}`} checked={choices[field] === side} onChange={() => setChoices({ ...choices, [field]: side })} /> {side === "local" ? t.workEditorKeepLocal : t.workEditorUseRemote}</label>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify(side === "local" ? draft[field] : draftFromWork(remote.work)[field], null, 2)}</pre>
        </div>)}</div>
      </fieldset>)}
      <button type="button" className={buttonClass} disabled={busy || changes.some(field => !choices[field])} onClick={() => {
        if (changes.some(field => choices[field] === "remote") && !window.confirm(t.workEditorDiscard)) return;
        const next = draftFromWork(remote.work);
        setDraft(reconcileWork(base, draft, next, choices)); setBase(next);
        setLoaded(remote.work); setEditorial(remote.metadata); setEtag(remote.etag ?? undefined); setRemote(undefined);
        setNeedsComparison(false); setFeedback({ ok: true, message: t.workEditorReconciled });
      }}>{t.workEditorApplyComparison}</button>
    </div>}
    <fieldset disabled={busy || Boolean(remote)} className="min-w-0 space-y-6">
      <legend className="sr-only">{t.workEditTitle}</legend>
      <WorkEditorFields draft={draft} onChange={value => { setDraft(value); setFeedback(undefined); }} locale={locale} errors={result.fieldErrors} />
    </fieldset>
    {loaded && <details><summary>{t.workEditorReadOnly}</summary><p>{t.workEditorReadOnlyHelp}</p>
      <pre className="overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify({ content: loaded.content, classification: loaded.classification, related_to: loaded.related_to, translation_of: loaded.translation_of }, null, 2)}</pre>
    </details>}
    <div className="flex flex-wrap justify-end gap-3">
      <a href={cancelHref} className={buttonClass}>{t.workFormCancel}</a>
      {mode === "create" && validate && <button type="button" className={buttonClass} disabled={busy} onClick={async () => {
        if (!form.current?.reportValidity()) return;
        setLoading(true);
        try { const data = new FormData(); data.set("locale", locale); data.set("payload", JSON.stringify(createPayload(draft))); setFeedback(await validate(data)); }
        catch { setFeedback({ ok: false, message: t.workEditorUnavailable }); }
        finally { setLoading(false); }
      }}>{t.workValidate}</button>}
      <button type="submit" disabled={busy || needsComparison || Boolean(remote) || (mode === "edit" && (!etag || !dirty))} className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white disabled:opacity-50">{t.workFormSave}</button>
    </div>
  </form>;
}
