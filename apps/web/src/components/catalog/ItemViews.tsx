import type { ItemEnvelope } from "../../server/catalog/items";
import { messages, type Locale } from "../../i18n/messages";

export function ItemDescription({ envelope, locale }: { envelope: ItemEnvelope; locale: Locale }) {
  const t = messages[locale], { item, metadata } = envelope;
  return <div className="space-y-6 break-words">
    <p>{t.itemSemanticHelp}</p>
    <dl><dt>{t.workIdentifier}</dt><dd>{item.internal_id}</dd><dt>{t.workUri}</dt><dd className="break-all">{item.uri}</dd></dl>
    <section><h2 className="text-xl font-semibold">{t.instanceIdentifiers}</h2><ul>{item.identified_by.map((identifier, i) => <li key={i}>{identifier.kind}: {identifier.value} {(identifier.source ?? []).map(source => source.uri).join(" / ")}</li>)}</ul></section>
    <section><h2 className="text-xl font-semibold">{t.itemCustodialHistory}</h2><ul>{item.custodial_history?.map((text, i) => <li key={i}>{text.value}{text.language ? ` (${text.language})` : ""}</li>)}</ul></section>
    <section><h2 className="text-xl font-semibold">{t.workFormNoteLabel}</h2><ul>{item.note?.map((note, i) => <li key={i}>{note.label.map(text => `${text.value}${text.language ? ` (${text.language})` : ""}`).join(" / ")} {(note.note_type ?? []).join(" / ")}</li>)}</ul></section>
    <section><h2 className="text-xl font-semibold">{t.editorial}</h2>{metadata ? <dl>
      <dt>{t.workState}</dt><dd>{metadata.editorialState ? t[`workEditorState_${metadata.editorialState}`] : t.workEditorUnknown}</dd>
      {metadata.candidateState && <><dt>{t.workEditorCandidate}</dt><dd>{t[`workEditorState_${metadata.candidateState}`]}</dd></>}
      <dt>{t.workEditorRevision}</dt><dd>{metadata.revision}</dd><dt>{t.workEditorProfile}</dt><dd className="break-all">{metadata.profile}</dd>
      <dt>{t.instanceCreatedAt}</dt><dd>{metadata.createdAt}</dd><dt>{t.instanceProvenance}</dt><dd>{metadata.provenance.source} / {metadata.provenance.process}{metadata.provenance.reason ? ` / ${metadata.provenance.reason}` : ""}</dd>
    </dl> : <p>{t.workEditorMetadataUnavailable}</p>}</section>
    <details><summary>{t.instanceReadOnly}</summary><p>{t.instanceProjectionHelp}</p><pre className="overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify(item, null, 2)}</pre></details>
  </div>;
}
