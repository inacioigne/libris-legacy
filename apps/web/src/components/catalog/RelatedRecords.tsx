import type { RelatedReference } from "../../server/catalog/related";
import { messages, type Locale } from "../../i18n/messages";

export function RelatedRecords({ references, collection, locale }: { references: RelatedReference[]; collection: "instances" | "items"; locale: Locale }) {
  const t = messages[locale];
  return <section className="space-y-3 rounded-xl border p-4">
    <h2 className="text-xl font-semibold">{collection === "instances" ? t.instanceLinked : t.items}</h2>
    {collection === "items" && <p>{t.itemSemanticHelp}</p>}
    {!references.length ? <p role="status">{collection === "instances" ? t.instanceEmpty : t.itemEmpty}</p> : <ul className="space-y-3">
      {references.map(reference => <li key={reference.internal_id} className="break-all">
        <a className="underline" href={`/${locale}/admin/catalog/${collection}/${encodeURIComponent(reference.internal_id!)}`}>{reference.uri ?? reference.internal_id}</a>
        <p>{t.workIdentifier}: {reference.internal_id}</p>
      </li>)}
    </ul>}
  </section>;
}
