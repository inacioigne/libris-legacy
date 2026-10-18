"use client";

import { useRef, useState } from "react";
import { authorityLabel, authorityLinks, referenceKey, sameLink, selectedLink, type Authority, type AuthorityType, type Link, type RelationCollection, type RelationEnvelope } from "../../catalog/authorities";
import type { AuthorityResult } from "../../server/catalog/authority-actions";
import type { AuthorityQuery } from "../../server/catalog/authorities";
import { messages, type Locale } from "../../i18n/messages";
import { AuthoritySelector } from "./AuthoritySelector";
import { buttonClass, EditorInput } from "./WorkEditorFields";

export function AuthorityReferenceView({ link, labels, locale }: { link: Link; labels: Record<string, string>; locale: Locale }) {
  const t = messages[locale], reference = link.target;
  return <div className="space-y-1 break-all">
    <p className="font-semibold">{labels[referenceKey(reference)] ?? (reference.kind === "unreconciled" ? reference.label : t.authorityUnavailableLabel)}</p>
    <p>{reference.kind === "local" ? t.authorityLocal : reference.kind === "external" ? t.authorityExternal : t.authorityUnreconciled} · {reference.entity_type}</p>
    {reference.kind === "unreconciled" ? <p>{reference.namespace} · {reference.key}</p> : <p>{reference.uri}{reference.kind === "local" && <><br />{reference.internal_id}</>}</p>}
    {link.kind === "contribution" && <p>{t.authorityRoles}: {link.role.join(" · ")}</p>}
    {link.kind === "publication" && <p>{t.authorityPublication}: {link.activity_key}</p>}
    <details><summary>{t.workEditorReadOnly}</summary><pre className="whitespace-pre-wrap">{JSON.stringify(link, null, 2)}</pre></details>
  </div>;
}

export function AuthorityRelations({ locale, collection, id, initial, canUpdate, canLookup, search, reload, mutate, onSaved }: {
  locale: Locale; collection: RelationCollection; id: string; initial: AuthorityResult; canUpdate: boolean; canLookup: boolean;
  search: (type: AuthorityType, query: AuthorityQuery, locale: Locale) => Promise<AuthorityResult>;
  reload: (collection: RelationCollection, id: string, locale: Locale) => Promise<AuthorityResult>;
  mutate: (collection: RelationCollection, id: string, op: "add" | "remove", link: Link, ifMatch: string, locale: Locale) => Promise<AuthorityResult>;
  onSaved?: () => void;
}) {
  const t = messages[locale];
  const [current, setCurrent] = useState(initial.envelope);
  const [remote, setRemote] = useState<RelationEnvelope>();
  const [feedback, setFeedback] = useState(initial);
  const [selected, setSelected] = useState<Authority>();
  const [type, setType] = useState<AuthorityType>("Agent");
  const [roles, setRoles] = useState<string[]>(["http://id.loc.gov/vocabulary/relators/aut"]);
  const [otherRoles, setOtherRoles] = useState("");
  const [activity, setActivity] = useState("__new__");
  const [busy, setBusy] = useState(false), [stale, setStale] = useState(false);
  const lock = useRef(false);
  const summary = useRef<HTMLDivElement>(null), selection = useRef<HTMLDivElement>(null), comparison = useRef<HTMLDivElement>(null);
  const links = current ? authorityLinks(current.relations) : [];
  const publications = [...new Set(links.flatMap(link => link.kind === "publication" ? [link.activity_key] : []))];
  const allRoles = [...new Set([...roles, ...otherRoles.trim().split(/\s+/).filter(Boolean)])];
  async function change(op: "add" | "remove", link: Link) {
    if (lock.current || stale || remote || !current?.etag || !canUpdate) return;
    if (op === "add" && links.some(existing => sameLink(existing, link))) {
      setFeedback({ ok: false, message: t.authorityDuplicate }); return;
    }
    if (op === "remove" && !window.confirm(t.authorityConfirmRemove)) return;
    lock.current = true; setBusy(true);
    try {
      const result = await mutate(collection, id, op, link, current.etag, locale);
      setFeedback(result);
      if (result.ok && result.envelope) {
        setCurrent(result.envelope); if (op === "add") setSelected(undefined);
        setActivity("__new__");
        onSaved?.();
      }
      if (result.status === 412) setStale(true);
    } catch { setFeedback({ ok: false, message: t.workEditorUnavailable }); }
    finally { lock.current = false; setBusy(false); requestAnimationFrame(() => summary.current?.focus()); }
  }
  async function load() {
    if (lock.current) return;
    lock.current = true; setBusy(true);
    try {
      const result = await reload(collection, id, locale);
      if (result.ok && result.envelope?.etag) { setRemote(result.envelope); setStale(true); requestAnimationFrame(() => comparison.current?.focus()); }
      else setFeedback(result.ok ? { ok: false, message: t.workEditorMissingVersion } : result);
    } catch { setFeedback({ ok: false, message: t.workEditorUnavailable }); }
    finally { lock.current = false; setBusy(false); }
  }
  return <section aria-busy={busy} className="my-6 space-y-4 rounded-xl border p-4">
    <h2 className="text-xl font-semibold">{t.authorityRelations}</h2><p>{t.authorityPolicy}</p>
    <div ref={summary} tabIndex={-1} role={feedback.ok ? "status" : "alert"}>
      {busy ? <p>{t.workEditorLoading}</p> : <p>{feedback.message}</p>}
      {feedback.issues?.map(issue => <p key={issue}>{issue}</p>)}{feedback.requestId && <p>{t.workFormRequestId}: {feedback.requestId}</p>}
      {feedback.status === 401 && <a className="underline" href={`/${locale}/admin`} target="_blank" rel="noopener noreferrer">{t.workEditorSignIn}</a>}
    </div>
    {current && (!links.length ? <p>{t.authorityEmptyLinks}</p> : <ul className="space-y-3">{links.map(link => <li key={JSON.stringify(link)} className="rounded-lg border p-3">
      <AuthorityReferenceView link={link} labels={current.labels} locale={locale} />
      {canUpdate && <button type="button" className={buttonClass} disabled={busy || stale || Boolean(remote) || !current.etag} onClick={() => change("remove", link)} aria-label={`${t.authorityRemove}: ${current.labels[referenceKey(link.target)] ?? referenceKey(link.target)}`}>{t.authorityRemove}</button>}
    </li>)}</ul>)}
    <button type="button" disabled={busy} className={buttonClass} onClick={load}>{t.authorityReload}</button>
    {remote && <div ref={comparison} tabIndex={-1} className="space-y-3 rounded-lg border-2 border-amber-600 p-4" aria-label={t.workEditorComparison}>
      <h3>{t.workEditorComparison}</h3><p>{t.authorityCompareHelp}</p>
      {!authorityLinks(remote.relations).length && <p>{t.authorityEmptyLinks}</p>}
      {authorityLinks(remote.relations).map(link => <AuthorityReferenceView key={JSON.stringify(link)} link={link} labels={remote.labels} locale={locale} />)}
      <button type="button" className={buttonClass} disabled={busy} onClick={() => { setCurrent(remote); setRemote(undefined); setStale(false); setActivity("__choose__"); setFeedback({ ok: true, message: t.workEditorReconciled }); }}>{t.authorityAdopt}</button>
    </div>}
    {!canUpdate && <p>{t.authorityReadOnly}</p>}
    {canUpdate && !canLookup && <p>{t.authorityLookupDenied}</p>}
    {canUpdate && canLookup && current && <fieldset disabled={busy || Boolean(remote)} className="min-w-0 space-y-4">
      <legend>{t.authorityAdd}</legend>
      {collection === "works" && <label>{t.authorityChoose} <select value={type} onChange={event => { setType(event.target.value as AuthorityType); setSelected(undefined); }}>
        <option value="Agent">{t.authorityAgent}</option><option value="Subject">{t.authoritySubject}</option>
      </select></label>}
      <AuthoritySelector key={type} locale={locale} type={type} search={search} organizationsOnly={collection === "items"} onChange={authority => { setSelected(authority); requestAnimationFrame(() => selection.current?.focus()); }} />
      {selected && <div ref={selection} tabIndex={-1} role="status" className="break-all rounded-lg border border-teal-600 p-3"><h3>{t.authoritySelected}</h3><p>{authorityLabel(selected)}</p><p>{selected.uri}</p><p>{selected.internal_id}</p></div>}
      {collection === "works" && type === "Agent" && <fieldset className="space-y-2"><legend>{t.authorityRoles}</legend><p>{t.authorityRoleHelp}</p>
        {([['aut', t.authorityAuthor], ['edt', t.authorityEditor], ['trl', t.authorityTranslator]] as const).map(([code, label]) => {
          const uri = `http://id.loc.gov/vocabulary/relators/${code}`;
          return <label key={code} className="mr-4 inline-block"><input type="checkbox" checked={roles.includes(uri)} onChange={event => setRoles(event.target.checked ? [...roles, uri] : roles.filter(role => role !== uri))} /> {label}</label>;
        })}
        <EditorInput label={t.authorityOtherRoles} value={otherRoles} onChange={setOtherRoles} path="authority-roles" context={{ locale }} />
      </fieldset>}
      {collection === "instances" && <label>{t.authorityPublication} <select value={activity} onChange={event => setActivity(event.target.value)}><option value="__choose__" disabled>{t.authorityChoose}</option><option value="__new__">{t.authorityNewPublication}</option>{publications.map((key, index) => <option key={key} value={key}>{t.authorityPublication} {index + 1}</option>)}</select></label>}
      <button type="button" className={buttonClass} disabled={busy || stale || !current.etag || !selected || (collection === "instances" && activity === "__choose__") || (collection === "works" && type === "Agent" && !allRoles.length)} onClick={() => {
        if (!selected) return;
        try { void change("add", selectedLink(current.relations.source, selected, allRoles, activity === "__new__" ? `publication-${crypto.randomUUID()}` : activity)); }
        catch { setFeedback({ ok: false, message: t.authorityInvalid }); }
      }}>{t.authorityAdd}</button>
    </fieldset>}
  </section>;
}
