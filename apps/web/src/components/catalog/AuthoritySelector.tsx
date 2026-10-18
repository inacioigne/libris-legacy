"use client";

import { useRef, useState } from "react";
import { authorityLabel, type Authority, type AuthorityType } from "../../catalog/authorities";
import type { AuthorityResult } from "../../server/catalog/authority-actions";
import type { AuthorityQuery } from "../../server/catalog/authorities";
import { messages, type Locale } from "../../i18n/messages";
import { buttonClass, EditorInput } from "./WorkEditorFields";

export function AuthoritySelector({ locale, type, search, onChange, disabled, organizationsOnly = false }: {
  locale: Locale; type: AuthorityType; disabled?: boolean; organizationsOnly?: boolean;
  search: (type: AuthorityType, query: AuthorityQuery, locale: Locale) => Promise<AuthorityResult>;
  onChange: (authority: Authority) => void;
}) {
  const t = messages[locale];
  const [id, setId] = useState(""), [uri, setUri] = useState(""), [label, setLabel] = useState("");
  const [query, setQuery] = useState<AuthorityQuery>({});
  const [result, setResult] = useState<AuthorityResult>();
  const [busy, setBusy] = useState(false);
  const results = useRef<HTMLDivElement>(null);
  async function load(cursor?: string) {
    setBusy(true);
    const next = cursor ? query : { internal_id: id, uri };
    try { setResult(await search(type, { ...next, cursor }, locale)); setQuery(next); }
    catch { setResult({ ok: false, message: t.workEditorUnavailable }); }
    finally { setBusy(false); requestAnimationFrame(() => results.current?.focus()); }
  }
  const items = result?.page?.items.filter(authority => (!organizationsOnly || ("kind" in authority && authority.kind === "Organization")) && authorityLabel(authority).toLocaleLowerCase(locale).includes(label.toLocaleLowerCase(locale)));
  return <fieldset disabled={disabled || busy} aria-busy={busy} className="min-w-0 space-y-3 rounded-xl border p-4">
    <legend>{organizationsOnly ? t.authorityHolder : type === "Agent" ? t.authorityAgent : t.authoritySubject}</legend>
    <p>{t.authoritySearchHelp}</p>
    <div className="grid gap-3 md:grid-cols-2">
      <EditorInput label={t.workIdentifier} value={id} onChange={setId} path="authority-id" context={{ locale }} />
      <EditorInput label={t.workUri} value={uri} onChange={setUri} path="authority-uri" context={{ locale }} />
    </div>
    <button type="button" className={buttonClass} onClick={() => load()}>{t.authorityBrowse}</button>
    <EditorInput label={t.authorityFilter} value={label} onChange={setLabel} path="authority-label" context={{ locale }} />
    {busy && <p role="status">{t.workEditorLoading}</p>}
    <div ref={results} tabIndex={-1} className="space-y-3" aria-label={type === "Agent" ? t.authorityAgent : t.authoritySubject}>
      {result?.message && <p role="alert">{result.message} {result.requestId}</p>}
      {items?.length === 0 && <p role="status">{t.authorityEmpty}</p>}
      {items && <ul className="space-y-3">{items.map(authority => <li key={authority.internal_id} className="break-all rounded-lg border p-3">
        <p className="font-semibold">{authorityLabel(authority)}</p><p>{t.authorityLocal}</p><p>{authority.uri}</p><p>{authority.internal_id}</p>
        <button type="button" className={buttonClass} aria-label={`${t.authorityChoose}: ${authorityLabel(authority)} — ${authority.uri}`} onClick={() => onChange(authority)}>{t.authorityChoose}</button>
      </li>)}</ul>}
      {result?.page?.next_cursor && <button type="button" className={buttonClass} onClick={() => load(result.page?.next_cursor ?? undefined)}>{t.nextPage}</button>}
    </div>
  </fieldset>;
}
