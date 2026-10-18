"use client";

import { useState } from "react";
import type { WorkRead } from "../../catalog/work-editor";
import type { InstanceDraft } from "../../catalog/instance-editor";
import type { WorkSelectionState } from "../../server/catalog/instance-actions";
import { messages, type Locale } from "../../i18n/messages";
import { buttonClass, EditorInput } from "./WorkEditorFields";

export function WorkSelector({ locale, value, onChange, search, disabled }: {
  locale: Locale; value: InstanceDraft["instance_of"]; onChange: (work: WorkRead) => void; disabled?: boolean;
  search: (query: { internal_id?: string; uri?: string; cursor?: string }, locale: Locale) => Promise<WorkSelectionState>;
}) {
  const t = messages[locale];
  const [id, setId] = useState(""), [uri, setUri] = useState("");
  const [filter, setFilter] = useState({ internal_id: "", uri: "" });
  const [result, setResult] = useState<WorkSelectionState>();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<WorkRead>();
  async function load(cursor?: string) {
    setBusy(true);
    const query = cursor ? filter : { internal_id: id, uri };
    try { setResult(await search({ ...query, cursor }, locale)); setFilter(query); }
    catch { setResult({ ok: false, message: t.workEditorUnavailable }); }
    finally { setBusy(false); }
  }
  return <fieldset disabled={disabled || busy} className="min-w-0 space-y-3 rounded-xl border border-slate-300 p-4" aria-busy={busy}>
    <legend className="px-2 font-semibold">{t.instanceWork}</legend><p>{t.instanceWorkHelp}</p>
    {value && <div role="status" className="break-all rounded-lg border border-teal-600 p-3">
      {selected && "uri" in value && value.uri === selected.uri && <p>{selected.title.flatMap(title => title.main_title.map(text => text.value)).join(" / ")}</p>}
      <p>{"internal_id" in value ? value.internal_id : ""}</p><p>{"uri" in value ? value.uri : "label" in value ? value.label : ""}</p>
    </div>}
    <div className="grid gap-3 md:grid-cols-2">
      <EditorInput label={t.workIdentifier} value={id} onChange={setId} path="work-search-id" context={{ locale }} />
      <EditorInput label={t.workUri} value={uri} onChange={setUri} path="work-search-uri" context={{ locale }} />
    </div>
    <button type="button" className={buttonClass} onClick={() => load()}>{t.instanceWorkSearch}</button>
    {busy && <p role="status">{t.workEditorLoading}</p>}
    {result?.message && <p role="alert">{result.message} {result.requestId}</p>}
    {result?.page && <div className="space-y-3">
      {!result.page.items.length && <p>{t.noWorks}</p>}
      {result.page.items.map(work => <div key={work.internal_id} className="space-y-2 break-all border-b p-3">
        <a href={`/${locale}/admin/catalog/works/${work.internal_id}`} target="_blank" rel="noopener noreferrer" className="underline">{work.title.flatMap(title => title.main_title.map(text => text.value)).join(" / ")}</a>
        <p>{work.internal_id}</p><p>{work.uri}</p>
        <button type="button" className={buttonClass} onClick={() => { setSelected(work); onChange(work); }}>{t.instanceChooseWork}</button>
      </div>)}
      {result.page.next_cursor && <button type="button" className={buttonClass} onClick={() => load(result.page!.next_cursor ?? undefined)}>{t.nextPage}</button>}
    </div>}
  </fieldset>;
}
