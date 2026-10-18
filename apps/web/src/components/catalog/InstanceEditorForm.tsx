"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { messages, type Locale } from "../../i18n/messages";
import { changedInstanceFields, instancePayload, draftFromInstance, reconcileInstance, instancePatch, type InstanceChoices, type InstanceRead } from "../../catalog/instance-editor";
import type { InstanceFormState, InstanceReloadState } from "../../server/catalog/instance-actions";
import type { InstanceEnvelope } from "../../server/catalog/instances";
import { buttonClass } from "./WorkEditorFields";
import { InstanceEditorFields } from "./InstanceEditorFields";
import { WorkSelector } from "./WorkSelector";
import type { WorkSelectionState } from "../../server/catalog/instance-actions";
import type { WorkRead } from "../../catalog/work-editor";

type Props = {
  locale: Locale;
  mode: "create" | "edit";
  action: (prevState: InstanceFormState | undefined, formData: FormData) => Promise<InstanceFormState>;
  reload?: (id: string, locale: Locale) => Promise<InstanceReloadState>;
  instance?: InstanceRead | null;
  instanceId?: string;
  metadata?: InstanceEnvelope["metadata"];
  ifMatch?: string;
  cancelHref: string;
  initialWork?: WorkRead;
  searchWorks: (query: { internal_id?: string; uri?: string; cursor?: string }, locale: Locale) => Promise<WorkSelectionState>;
  validate?: (data: FormData) => Promise<InstanceFormState>;
};

export function InstanceEditorForm({ locale, mode, action, reload, instance, instanceId, ifMatch, metadata, cancelHref, initialWork, searchWorks, validate }: Props) {
  const t = messages[locale];
  const [state, formAction, pending] = useActionState(action, { ok: true });
  const [base, setBase] = useState(() => draftFromInstance(instance));
  const [draft, setDraft] = useState(() => {
    const value = draftFromInstance(instance);
    if (!instance && initialWork) value.instance_of = { kind: "local", entity_type: "Work", internal_id: initialWork.internal_id, uri: initialWork.uri };
    return value;
  });
  const [editorial, setEditorial] = useState(metadata);
  const [loaded, setLoaded] = useState(instance);
  const [etag, setEtag] = useState(ifMatch);
  const [remote, setRemote] = useState<InstanceEnvelope>();
  const [choices, setChoices] = useState<InstanceChoices>({});
  const [feedback, setFeedback] = useState<InstanceFormState>();
  const [loading, setLoading] = useState(false);
  const [needsComparison, setNeedsComparison] = useState(false);
  const savedNavigation = useRef(false);
  const form = useRef<HTMLFormElement>(null);
  const summary = useRef<HTMLDivElement>(null);
  const comparison = useRef<HTMLDivElement>(null);
  const changes = changedInstanceFields(base, draft);
  const dirty = changes.length > 0;
  const result = feedback ?? state;
  const busy = pending || loading;
  const labels = { instance_of: t.instanceWork, title: t.workEditorTitles, edition_statement: t.instanceEdition, provision_activity: t.instancePublication, identified_by: t.instanceIdentifiers, extent: t.instanceExtent, carrier: t.instanceCarrier, note: t.workFormNoteLabel };

  useEffect(() => {
    if (state.saved) {
      setBase(draftFromInstance(state.saved.instance)); setDraft(draftFromInstance(state.saved.instance));
      setLoaded(state.saved.instance); setEditorial(state.saved.metadata); setEtag(state.saved.etag ?? undefined);
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
    if (!reload || !instanceId) return;
    setLoading(true);
    try {
      const response = await reload(instanceId, locale);
      if (response.remote?.etag && response.remote.instance.internal_id === instanceId) {
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
        setFeedback(current => ({ ok: false, message: t.instanceValidationError, fieldErrors: { ...current?.fieldErrors, [path]: t.workEditorRequired } }));
      }
    }}
    onSubmit={event => {
      if (busy || needsComparison || remote) { event.preventDefault(); return; }
      setFeedback(undefined);
      if (!draft.instance_of) {
        event.preventDefault(); setFeedback({ ok: false, message: t.instanceWorkRequired });
      }
    }}>
    <input type="hidden" name="locale" value={locale} />
    {instanceId && <input type="hidden" name="id" value={instanceId} />}
    {etag && <input type="hidden" name="ifMatch" value={etag} />}
    <input type="hidden" name="payload" value={JSON.stringify(mode === "create" ? draft : instancePatch(base, draft))} />
    <p role="status">{busy ? (pending ? t.workFormLoading : t.workEditorLoading) : dirty ? t.workEditorDirty : t.workEditorUnchanged}</p>
    {loaded && <dl className="break-all text-sm"><dt>{t.workIdentifier}</dt><dd>{loaded.internal_id}</dd><dt>{t.workUri}</dt><dd>{loaded.uri}</dd></dl>}
    {loaded && (editorial ? <dl className="break-all text-sm">
      <dt>{t.workState}</dt><dd>{editorial.editorialState ? t[`workEditorState_${editorial.editorialState}`] : t.workEditorUnknown}</dd>
      {editorial.candidateState && <><dt>{t.workEditorCandidate}</dt><dd>{t[`workEditorState_${editorial.candidateState}`]}</dd></>}
      <dt>{t.workEditorRevision}</dt><dd>{editorial.revision}</dd><dt>{t.workEditorProfile}</dt><dd>{editorial.profile}</dd>
    </dl> : <p>{t.workEditorMetadataUnavailable}</p>)}
    <p className="text-sm">{t.instancePreservation}</p>
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
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify(side === "local" ? draft[field] : draftFromInstance(remote.instance)[field], null, 2)}</pre>
        </div>)}</div>
      </fieldset>)}
      <button type="button" className={buttonClass} disabled={busy || changes.some(field => !choices[field])} onClick={() => {
        if (changes.some(field => choices[field] === "remote") && !window.confirm(t.workEditorDiscard)) return;
        const next = draftFromInstance(remote.instance);
        setDraft(reconcileInstance(base, draft, next, choices)); setBase(next);
        setLoaded(remote.instance); setEditorial(remote.metadata); setEtag(remote.etag ?? undefined); setRemote(undefined);
        setNeedsComparison(false); setFeedback({ ok: true, message: t.workEditorReconciled });
      }}>{t.workEditorApplyComparison}</button>
    </div>}
    <fieldset disabled={busy || Boolean(remote)} className="min-w-0 space-y-6">
      <legend className="sr-only">{t.instanceEdit}</legend>
      <WorkSelector locale={locale} value={draft.instance_of} search={searchWorks} onChange={work => {
        setDraft({ ...draft, instance_of: { kind: "local", entity_type: "Work", internal_id: work.internal_id, uri: work.uri } }); setFeedback(undefined);
      }} />
      <InstanceEditorFields draft={draft} onChange={value => { setDraft(value); setFeedback(undefined); }} locale={locale} errors={result.fieldErrors} />
    </fieldset>
    {mode === "create" && validate && <button type="button" className={buttonClass} disabled={busy || !draft.instance_of} onClick={async () => {
      if (!form.current?.reportValidity()) return;
      setLoading(true);
      try {
        const data = new FormData(); data.set("locale", locale); data.set("payload", JSON.stringify(instancePayload(draft)));
        setFeedback(await validate(data));
      } catch { setFeedback({ ok: false, message: t.workEditorUnavailable }); }
      finally { setLoading(false); }
    }}>{t.instanceValidate}</button>}
    <div className="flex flex-wrap justify-end gap-3">
      <a href={cancelHref} className={buttonClass}>{t.workFormCancel}</a>
      <button type="submit" disabled={busy || needsComparison || Boolean(remote) || (mode === "edit" && (!etag || !dirty))} className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white disabled:opacity-50">{t.instanceSave}</button>
    </div>
  </form>;
}
