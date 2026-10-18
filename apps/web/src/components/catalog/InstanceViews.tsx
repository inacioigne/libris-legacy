import type { InstanceRead } from "../../catalog/instance-editor";
import type { InstanceEnvelope, InstancePage } from "../../server/catalog/instances";
import { messages, type Locale } from "../../i18n/messages";

export function instanceTitle(instance: InstanceRead, locale: Locale) {
  return (instance.title ?? []).flatMap(title => title.main_title.map(text => text.value)).join(" / ") || messages[locale].instanceUntitled;
}
export function instanceWorkHref(instance: InstanceRead, locale: Locale) {
  const ref = instance.instance_of;
  if ("kind" in ref && ref.kind === "local" && ref.internal_id) return `/${locale}/admin/catalog/works/${encodeURIComponent(ref.internal_id)}`;
  if ("uri" in ref && ref.uri) return `/${locale}/admin/catalog/works?uri=${encodeURIComponent(ref.uri)}`;
  return undefined;
}
export function InstanceList({ page, locale, canCreate }: { page: InstancePage; locale: Locale; canCreate: boolean }) {
  const t = messages[locale];
  return <section className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-3xl font-semibold">{t.instances}</h1>
      {canCreate && <a className="rounded-lg bg-teal-700 px-4 py-2 text-white" href={`/${locale}/admin/catalog/instances/new`}>{t.instanceNew}</a>}
    </div>
    {!page.items.length ? <p role="status">{t.instanceEmpty}</p> : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <caption className="sr-only">{t.instances}</caption><thead><tr>{[t.workTitle, t.workIdentifier, t.instanceEdition, t.instanceIdentifiers, t.instanceWork].map(label => <th key={label} scope="col" className="p-3">{label}</th>)}</tr></thead>
      <tbody>{page.items.map(instance => <tr key={instance.internal_id} className="border-t">
        <td className="p-3"><a className="underline" href={`/${locale}/admin/catalog/instances/${instance.internal_id}`}>{instanceTitle(instance, locale)}</a></td>
        <td className="break-all p-3">{instance.internal_id}</td><td className="p-3">{instance.edition_statement?.map(text => text.value).join(" / ") || "—"}</td>
        <td className="p-3">{instance.identified_by?.map(identifier => identifier.value).join(" / ") || "—"}</td>
        <td className="break-all p-3">{instanceWorkHref(instance, locale) ? <a className="underline" href={instanceWorkHref(instance, locale)}>{"uri" in instance.instance_of ? instance.instance_of.uri : t.instanceWork}</a> : t.instanceWorkUnavailable}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
export function InstanceDescription({ envelope, locale }: { envelope: InstanceEnvelope; locale: Locale }) {
  const t = messages[locale], { instance, metadata } = envelope;
  const sections = [
    [t.instanceEdition, instance.edition_statement?.map(text => `${text.value}${text.language ? ` (${text.language})` : ""}`)],
    [t.instanceIdentifiers, instance.identified_by?.map(identifier => `${identifier.kind ?? "Identifier"}: ${identifier.value} ${(identifier.source ?? []).map(source => source.uri).join(" / ")}`)],
    [t.instanceExtent, instance.extent?.flatMap(extent => extent.label.map(text => `${text.value}${text.language ? ` (${text.language})` : ""}`))],
    [t.instanceCarrier, instance.carrier?.map(carrier => carrier.uri)],
    [t.workFormNoteLabel, instance.note?.flatMap(note => note.label.map(text => `${text.value}${text.language ? ` (${text.language})` : ""} ${(note.note_type ?? []).join(" / ")}`))],
  ] as const;
  return <div className="space-y-6 break-words">
    <section><h2 className="text-xl font-semibold">{t.instanceBibliographic}</h2><dl className="space-y-3">
      {sections.map(([label, values]) => <div key={label}><dt className="font-medium">{label}</dt><dd>{values?.length ? <ul>{values.map((value, i) => <li key={i}>{value}</li>)}</ul> : "—"}</dd></div>)}
      <div><dt className="font-medium">{t.instancePublication}</dt><dd>{instance.provision_activity?.map((publication, i) => <dl key={i} className="my-3 border p-3">
        <dt>{t.workFormContributorAgentLabel}</dt><dd>{publication.agent?.map(agent => "uri" in agent ? agent.uri : "label" in agent ? agent.label : agent.internal_id).join(" / ") || "—"}</dd>
        <dt>{t.instancePlace}</dt><dd>{publication.place?.map(place => place.uri).join(" / ") || "—"}</dd>
        <dt>{t.instanceDate}</dt><dd>{publication.date?.map(date => `${date.value} (${date.datatype ?? "http://www.w3.org/2001/XMLSchema#string"})`).join(" / ") || "—"}</dd>
      </dl>)}</dd></div>
    </dl></section>
    <section><h2 className="text-xl font-semibold">{t.instanceIdentity}</h2><dl className="break-all"><dt>{t.workIdentifier}</dt><dd>{instance.internal_id}</dd><dt>{t.workUri}</dt><dd>{instance.uri}</dd></dl></section>
    <section><h2 className="text-xl font-semibold">{t.editorial}</h2>{metadata ? <dl>
      <dt>{t.workState}</dt><dd>{metadata.editorialState ? t[`workEditorState_${metadata.editorialState}`] : t.workEditorUnknown}</dd>
      {metadata.candidateState && <><dt>{t.workEditorCandidate}</dt><dd>{t[`workEditorState_${metadata.candidateState}`]}</dd></>}
      <dt>{t.workEditorRevision}</dt><dd>{metadata.revision}</dd><dt>{t.workEditorProfile}</dt><dd className="break-all">{metadata.profile}</dd>
      <dt>{t.instanceCreatedAt}</dt><dd>{metadata.createdAt}</dd><dt>{t.instanceProvenance}</dt><dd>{metadata.provenance.source} / {metadata.provenance.process}{metadata.provenance.reason ? ` / ${metadata.provenance.reason}` : ""}</dd>
    </dl> : <p>{t.workEditorMetadataUnavailable}</p>}</section>
    <details><summary>{t.instanceReadOnly}</summary><p>{t.instanceProjectionHelp}</p><pre className="overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify(instance, null, 2)}</pre></details>
  </div>;
}
