"use client";

import Link from "next/link";
import { useActionState, useEffect, useRef, useState } from "react";
import { newSubjectDraft, subjectDraftFromRead, type SubjectRead } from "../../catalog/subject-editor";
import { messages, type Locale } from "../../i18n/messages";
import type { SubjectFormState } from "../../server/catalog/subject-actions";
import { EditorInput, Rows, Texts, buttonClass } from "./WorkEditorFields";

export function SubjectEditorForm({ locale, action, subject, ifMatch, reload, canBrowse = true }: { locale: Locale; action: (previous: SubjectFormState, data: FormData) => Promise<SubjectFormState>; subject?: SubjectRead; ifMatch?: string; reload?: (id: string, locale: Locale) => Promise<SubjectFormState>; canBrowse?: boolean }) {
  const t = messages[locale], edit = Boolean(subject), initial = subject ? subjectDraftFromRead(subject) : newSubjectDraft();
  const [base, setBase] = useState(initial), [draft, setDraft] = useState(initial), [etag, setEtag] = useState(ifMatch), [remote, setRemote] = useState<SubjectFormState["remote"]>();
  const [state, formAction, pending] = useActionState(action, { ok: true }); const summary = useRef<HTMLDivElement>(null); const context = { locale };
  useEffect(() => { if (state.message) summary.current?.focus(); }, [state]);
  useEffect(() => { if (state.envelope) { const saved = subjectDraftFromRead(state.envelope.subject); setBase(saved); setDraft(saved); setEtag(state.envelope.etag ?? undefined); setRemote(undefined); } }, [state.envelope]);
  if (!edit && state.saved) return <div ref={summary} tabIndex={-1} role="status" className="mt-8 space-y-4 rounded-2xl border border-teal-500 p-6"><h2 className="text-xl font-semibold">{state.message}</h2><p>{state.saved.preferred_label.map(label => label.value).join(" / ")}</p><p className="break-all">{state.saved.internal_id}<br />{state.saved.uri}</p><p>{t.agentFormDraftHelp}</p>{canBrowse && <Link className={buttonClass} href={`/${locale}/admin/authority/subjects/${state.saved.internal_id}`}>{t.subjectView}</Link>}</div>;
  const references = [["scheme", t.subjectSchemes], ["broader", t.subjectBroader], ["related", t.subjectRelated], ["external_reference", t.agentExternalReferences], ["provenance", t.agentProvenance]] as const;
  return <form action={formAction} aria-busy={pending} className="mt-8 space-y-6 rounded-2xl border p-6">
    <input type="hidden" name="locale" value={locale} />{subject && <input type="hidden" name="id" value={subject.internal_id} />}{etag && <input type="hidden" name="ifMatch" value={etag} />}<input type="hidden" name="payload" value={JSON.stringify(draft)} />
    {state.message && <div ref={summary} tabIndex={-1} role={state.ok ? "status" : "alert"} className="space-y-2 rounded-lg border p-4"><p>{state.message}</p>{state.issues?.map(issue => <p key={issue}>{issue}</p>)}{state.requestId && <p>{t.workFormRequestId}: {state.requestId}</p>}
      {state.status === 412 && reload && subject && <button type="button" className={buttonClass} onClick={async () => setRemote((await reload(subject.internal_id, locale)).remote)}>{t.workEditorLoadCurrent}</button>}</div>}
    {remote && <section className="space-y-3 rounded-lg border-2 border-amber-600 p-4"><h2 className="font-semibold">{t.workEditorComparison}</h2><p>{t.agentConflictHelp}</p><div className="grid gap-3 md:grid-cols-2"><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(draft, null, 2)}</pre><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(subjectDraftFromRead(remote.subject), null, 2)}</pre></div><div className="flex gap-3"><button type="button" className={buttonClass} onClick={() => { setBase(subjectDraftFromRead(remote.subject)); setEtag(remote.etag ?? undefined); setRemote(undefined); }}>{t.agentKeepLocalWithCurrentVersion}</button><button type="button" className={buttonClass} onClick={() => { const current = subjectDraftFromRead(remote.subject); setBase(current); setDraft(current); setEtag(remote.etag ?? undefined); setRemote(undefined); }}>{t.agentUseCurrentVersion}</button></div></section>}
    <Texts label={t.subjectPreferredLabel} values={draft.preferred_label} onChange={preferred_label => setDraft({ ...draft, preferred_label })} path="/preferred_label" context={context} />
    <Texts label={t.subjectAlternativeLabel} values={draft.alternative_label} onChange={alternative_label => setDraft({ ...draft, alternative_label })} path="/alternative_label" context={context} />
    <p className="text-sm">{t.subjectReferenceHelp}</p>{references.map(([field, label]) => <Rows key={field} label={label} values={draft[field]} onChange={values => setDraft({ ...draft, [field]: values })} empty={() => ({ uri: "" })} locale={locale}>{(reference, index, change) => <EditorInput label="URI" value={reference.uri} onChange={uri => change({ uri })} path={`/${field}/${index}/uri`} required context={context} />}</Rows>)}
    <div className="flex gap-3"><button className="rounded-lg bg-teal-700 px-4 py-2 text-white disabled:opacity-50" disabled={pending || Boolean(remote) || (edit && JSON.stringify(base) === JSON.stringify(draft))}>{pending ? t.agentFormSaving : edit ? t.subjectFormUpdate : t.subjectFormSave}</button><Link className={buttonClass} href={subject ? `/${locale}/admin/authority/subjects/${subject.internal_id}` : canBrowse ? `/${locale}/admin/authority/subjects` : `/${locale}/admin`}>{t.workFormCancel}</Link></div>
  </form>;
}
