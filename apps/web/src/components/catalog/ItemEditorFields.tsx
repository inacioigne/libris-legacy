"use client";
import type { ItemDraft } from "../../catalog/item-editor";
import { messages, type Locale } from "../../i18n/messages";
import { EditorInput, Rows, Texts, NoteFields } from "./WorkEditorFields";

export function ItemEditorFields({ draft, onChange, locale, errors }: {
  draft: ItemDraft; onChange: (draft: ItemDraft) => void; locale: Locale; errors?: Record<string, string>;
}) {
  const t = messages[locale], context = { locale, errors };
  return <>
    <p>{t.itemIdentifierHelp}</p>
    <Rows<ItemDraft["identified_by"][number]> label={t.instanceIdentifiers} values={draft.identified_by} onChange={identified_by => onChange({ ...draft, identified_by })} empty={() => ({ kind: "Local", value: "", source: [] })} locale={locale}>
      {(identifier, index, change) => <>
        <label>{t.instanceIdentifierKind}<select className="block w-full rounded border p-2 dark:bg-gray-950" value={identifier.kind ?? "Identifier"} onChange={event => change({ ...identifier, kind: event.target.value as typeof identifier.kind })}>
          <option value="Identifier">{t.instanceIdentifierGeneric}</option><option value="Local">{t.instanceIdentifierLocal}</option><option value="Isbn">ISBN</option>
        </select></label>
        <EditorInput label={t.instanceIdentifierValue} value={identifier.value} onChange={value => change({ ...identifier, value })} path={`/identified_by/${index}/value`} required context={context} />
        <Rows label={t.instanceSource} values={identifier.source ?? []} onChange={source => change({ ...identifier, source })} empty={() => ({ uri: "" })} locale={locale}>
          {(source, i, update) => <EditorInput label={t.instanceSource} value={source.uri} onChange={uri => update({ ...source, uri })} path={`/identified_by/${index}/source/${i}/uri`} required context={context} />}
        </Rows>
      </>}
    </Rows>
    <Texts label={t.itemCustodialHistory} values={draft.custodial_history} onChange={custodial_history => onChange({ ...draft, custodial_history })} path="/custodial_history" context={context} />
    <NoteFields values={draft.note} onChange={note => onChange({ ...draft, note })} locale={locale} errors={errors} />
  </>;
}
