"use client";

import Link from "next/link";
import { useActionState, useEffect, useRef, useState } from "react";
import { agentDraftFromRead, newAgentDraft, type AgentDraft, type AgentRead } from "../../catalog/agent-editor";
import { messages, type Locale } from "../../i18n/messages";
import type { AgentFormState } from "../../server/catalog/agent-actions";
import { EditorInput, Rows, Texts, buttonClass } from "./WorkEditorFields";

export function AgentCreateForm({ locale, action, canBrowse, agent, ifMatch, reload }: {
  locale: Locale;
  action: (previous: AgentFormState, data: FormData) => Promise<AgentFormState>;
  canBrowse: boolean;
  agent?: AgentRead;
  ifMatch?: string;
  reload?: (id: string, locale: Locale) => Promise<AgentFormState>;
}) {
  const t = messages[locale];
  const edit = Boolean(agent);
  const initialDraft = agent ? agentDraftFromRead(agent) : newAgentDraft();
  const [base, setBase] = useState(initialDraft);
  const [draft, setDraft] = useState(initialDraft);
  const [etag, setEtag] = useState(ifMatch);
  const [remote, setRemote] = useState<AgentFormState["remote"]>();
  const [reloading, setReloading] = useState(false);
  const [state, formAction, pending] = useActionState(action, { ok: true });
  const summary = useRef<HTMLDivElement>(null);
  const context = { locale };
  const inputClass = "w-full rounded-lg border border-slate-400 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-950";
  const kinds = { Agent: t.agentGeneric, Person: t.agentPerson, Organization: t.agentOrganization };

  useEffect(() => {
    if (state.message) summary.current?.focus();
  }, [state]);
  useEffect(() => {
    if (!state.envelope) return;
    const saved = agentDraftFromRead(state.envelope.agent);
    setBase(saved);
    setDraft(saved);
    setEtag(state.envelope.etag ?? undefined);
    setRemote(undefined);
  }, [state.envelope]);

  if (!edit && state.saved) return (
    <div ref={summary} tabIndex={-1} role="status" className="mt-8 space-y-4 rounded-2xl border border-teal-500 p-6">
      <h2 className="text-xl font-semibold">{state.message}</h2>
      <p>{state.saved.authorized_name.map(name => name.value).join(" / ")}</p>
      <dl className="space-y-2 break-all">
        <div><dt className="font-semibold">UUID</dt><dd>{state.saved.internal_id}</dd></div>
        <div><dt className="font-semibold">URI</dt><dd>{state.saved.uri}</dd></div>
      </dl>
      <p>{t.agentFormDraftHelp}</p>
      {canBrowse && <Link className="inline-block font-semibold text-teal-700 hover:underline dark:text-teal-300"
        href={`/${locale}/admin/authority/${state.saved.internal_id}`}>{t.authorityBrowse}</Link>}
      <div><a className={buttonClass} href={`/${locale}/admin/authority/new`}>{t.agentCreateAnother}</a></div>
    </div>
  );

  const references = [
    ["external_reference", t.agentExternalReferences],
    ["provenance", t.agentProvenance],
    ["related_authority", t.agentRelatedAuthorities],
  ] as const;

  return (
    <form action={formAction} aria-busy={pending} className="mt-8 space-y-6 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
      <input type="hidden" name="locale" value={locale} />
      {agent && <input type="hidden" name="id" value={agent.internal_id} />}
      {etag && <input type="hidden" name="ifMatch" value={etag} />}
      <input type="hidden" name="payload" value={JSON.stringify(draft)} />
      {state.message && <div ref={summary} tabIndex={-1} role={state.ok ? "status" : "alert"} className="space-y-2 rounded-lg border border-red-300 p-4">
        <p>{state.message}</p>
        {!!state.issues?.length && <ul className="list-disc pl-5">{state.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}
        {state.requestId && <p className="text-xs">{t.workFormRequestId}: {state.requestId}</p>}
        {state.status === 401 && <a href={`/${locale}/admin/login`} target="_blank" rel="noopener noreferrer" className="font-semibold underline">{t.login}</a>}
        {state.status === 403 && <p>{t.workEditorPermissionHelp}</p>}
        {state.status === 412 && reload && agent && <button type="button" className={buttonClass} disabled={reloading} onClick={async () => {
          setReloading(true);
          const result = await reload(agent.internal_id, locale);
          setRemote(result.remote);
          setReloading(false);
        }}>{reloading ? t.workEditorLoading : t.workEditorLoadCurrent}</button>}
      </div>}
      {remote && <section className="space-y-3 rounded-lg border-2 border-amber-600 p-4" aria-label={t.workEditorComparison}>
        <h2 className="text-lg font-semibold">{t.workEditorComparison}</h2>
        <p>{t.agentConflictHelp}</p>
        <div className="grid gap-3 md:grid-cols-2"><div><h3>{t.workEditorKeepLocal}</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(draft, null, 2)}</pre></div>
          <div><h3>{t.workEditorUseRemote}</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(agentDraftFromRead(remote.agent), null, 2)}</pre></div></div>
        <div className="flex flex-wrap gap-3"><button type="button" className={buttonClass} onClick={() => { setBase(agentDraftFromRead(remote.agent)); setEtag(remote.etag ?? undefined); setRemote(undefined); }}>{t.agentKeepLocalWithCurrentVersion}</button>
          <button type="button" className={buttonClass} onClick={() => { const current = agentDraftFromRead(remote.agent); setBase(current); setDraft(current); setEtag(remote.etag ?? undefined); setRemote(undefined); }}>{t.agentUseCurrentVersion}</button></div>
      </section>}
      <fieldset disabled={pending} className="space-y-6 disabled:opacity-60">
        <label className="block space-y-2 text-sm font-medium">
          <span>{t.agentKind}</span>
          <select name="kind" value={draft.kind} disabled={edit} onChange={event => setDraft({ ...draft, kind: event.target.value as AgentDraft["kind"] })} className={inputClass}>
            {(["Person", "Organization", "Agent"] as const).map(kind => <option key={kind} value={kind}>{kinds[kind]}</option>)}
          </select>
        </label>
        <p className="text-sm text-slate-600 dark:text-slate-300">{t.agentNameHelp}</p>
        <Texts label={t.agentAuthorizedName} values={draft.authorized_name} onChange={authorized_name => setDraft({ ...draft, authorized_name })} path="/authorized_name" context={context} />
        <Texts label={t.agentVariantNames} values={draft.variant_name} onChange={variant_name => setDraft({ ...draft, variant_name })} path="/variant_name" context={context} />
        <Rows label={t.agentIdentifiers} values={draft.identified_by} onChange={identified_by => setDraft({ ...draft, identified_by })}
          empty={() => ({ kind: "Identifier" as const, value: "", source: [] })} locale={locale}>
          {(identifier, index, change) => <div className="space-y-3">
            <label className="block space-y-2 text-sm font-medium">
              <span>{t.agentIdentifierKind}</span>
              <select value={identifier.kind ?? "Identifier"} onChange={event => change({ ...identifier, kind: event.target.value as typeof identifier.kind })} className={inputClass}>
                <option value="Identifier">{t.agentIdentifierGeneric}</option>
                <option value="Local">{t.agentIdentifierLocal}</option>
                <option value="Isbn">ISBN</option>
              </select>
            </label>
            <EditorInput label={t.agentIdentifierValue} value={identifier.value} onChange={value => change({ ...identifier, value })} path={`/identified_by/${index}/value`} required context={context} />
            <Rows label={t.agentIdentifierSources} values={identifier.source ?? []} onChange={source => change({ ...identifier, source })} empty={() => ({ uri: "" })} locale={locale}>
              {(source, sourceIndex, update) => <EditorInput label="URI" value={source.uri} onChange={uri => update({ uri })} path={`/identified_by/${index}/source/${sourceIndex}/uri`} required context={context} />}
            </Rows>
          </div>}
        </Rows>
        <p className="text-sm text-slate-600 dark:text-slate-300">{t.agentReferenceHelp}</p>
        {references.map(([field, label]) => (
          <Rows key={field} label={label} values={draft[field]} onChange={values => setDraft({ ...draft, [field]: values })} empty={() => ({ uri: "" })} locale={locale}>
            {(reference, index, change) => <EditorInput label="URI" value={reference.uri ?? ""} onChange={uri => change({ uri })} path={`/${field}/${index}/uri`} required context={context} />}
          </Rows>
        ))}
        <div className="flex flex-wrap gap-3">
          <button type="submit" className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800 disabled:opacity-50" disabled={pending || Boolean(remote) || (edit && JSON.stringify(base) === JSON.stringify(draft)) || draft.authorized_name.length === 0}>
            {pending ? t.agentFormSaving : edit ? t.agentFormUpdate : t.agentFormSave}
          </button>
          <Link href={agent ? `/${locale}/admin/authority/${agent.internal_id}` : canBrowse ? `/${locale}/admin/authority` : `/${locale}/admin`} className={buttonClass}>{t.workFormCancel}</Link>
        </div>
      </fieldset>
    </form>
  );
}
