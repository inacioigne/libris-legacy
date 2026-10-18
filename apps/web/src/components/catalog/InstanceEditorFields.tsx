"use client";
import type { InstanceDraft } from "../../catalog/instance-editor";
import { messages, type Locale } from "../../i18n/messages";
import { EditorInput, Rows, Texts, ReferenceInput, TitleFields, NoteFields } from "./WorkEditorFields";

export function InstanceEditorFields({ draft, onChange, locale, errors }: {
  draft: InstanceDraft; onChange: (draft: InstanceDraft) => void; locale: Locale; errors?: Record<string, string>;
}) {
  const t = messages[locale], context = { locale, errors };
  return <>
    <TitleFields values={draft.title} onChange={title => onChange({ ...draft, title })} locale={locale} errors={errors} />
    <Texts label={t.instanceEdition} values={draft.edition_statement} onChange={edition_statement => onChange({ ...draft, edition_statement })} path="/edition_statement" context={context} />
    <Rows<InstanceDraft["provision_activity"][number]> label={t.instancePublication} values={draft.provision_activity} onChange={provision_activity => onChange({ ...draft, provision_activity })} empty={() => ({ agent: [], place: [], date: [] })} locale={locale}>
      {(publication, index, change) => <>
        <p className="text-sm">{t.instanceAgentHelp}</p>
        <Rows<NonNullable<typeof publication.agent>[number]> label={t.workFormContributorAgentLabel} values={publication.agent ?? []} onChange={agent => change({ ...publication, agent })} empty={() => ({ uri: "" })} locale={locale}>
          {(agent, i, update) => <ReferenceInput reference={agent} onChange={update} label={t.workFormContributorAgentLabel} path={`/provision_activity/${index}/agent/${i}`} context={context} />}
        </Rows>
        <Rows label={t.instancePlace} values={publication.place ?? []} onChange={place => change({ ...publication, place })} empty={() => ({ uri: "" })} locale={locale}>
          {(place, i, update) => <EditorInput label={t.instancePlace} value={place.uri} onChange={uri => update({ ...place, uri })} path={`/provision_activity/${index}/place/${i}/uri`} required context={context} />}
        </Rows>
        <Rows<NonNullable<typeof publication.date>[number]> label={t.instanceDate} values={publication.date ?? []} onChange={date => change({ ...publication, date })} empty={() => ({ value: "", datatype: "http://www.w3.org/2001/XMLSchema#string" })} locale={locale}>
          {(date, i, update) => <div className="grid gap-3 md:grid-cols-2">
            <EditorInput label={t.instanceDate} value={date.value} onChange={value => update({ ...date, value })} path={`/provision_activity/${index}/date/${i}/value`} required context={context} />
            <label>{t.instanceDateType}<select className="block w-full rounded border p-2 dark:bg-gray-950" value={date.datatype ?? "http://www.w3.org/2001/XMLSchema#string"} onChange={event => update({ ...date, datatype: event.target.value as typeof date.datatype })}>
              <option value="http://www.w3.org/2001/XMLSchema#string">{t.instanceDateText}</option>
              <option value="http://www.w3.org/2001/XMLSchema#gYear">{t.instanceDateYear}</option>
              <option value="http://www.w3.org/2001/XMLSchema#date">{t.instanceDateCalendar}</option>
            </select></label>
          </div>}
        </Rows>
      </>}
    </Rows>
    <Rows<InstanceDraft["identified_by"][number]> label={t.instanceIdentifiers} values={draft.identified_by} onChange={identified_by => onChange({ ...draft, identified_by })} empty={() => ({ kind: "Isbn", value: "", source: [] })} locale={locale}>
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
    <Rows<InstanceDraft["extent"][number]> label={t.instanceExtent} values={draft.extent} onChange={extent => onChange({ ...draft, extent })} empty={() => ({ label: [{ value: "" }] })} locale={locale}>
      {(extent, index, change) => <Texts label={t.instanceExtent} values={extent.label} onChange={label => change({ ...extent, label })} path={`/extent/${index}/label`} context={context} />}
    </Rows>
    <Rows label={t.instanceCarrier} values={draft.carrier} onChange={carrier => onChange({ ...draft, carrier })} empty={() => ({ uri: "" })} locale={locale}>
      {(carrier, index, change) => <EditorInput label={t.instanceCarrier} value={carrier.uri} onChange={uri => change({ ...carrier, uri })} path={`/carrier/${index}/uri`} required context={context} />}
    </Rows>
    <NoteFields values={draft.note} onChange={note => onChange({ ...draft, note })} locale={locale} errors={errors} />
  </>;
}
