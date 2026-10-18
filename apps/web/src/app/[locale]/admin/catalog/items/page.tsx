import { notFound } from "next/navigation";
import { isLocale } from "@/i18n/messages";
import { itemPage } from "@/server/catalog/item-page";
import { listItems } from "@/server/catalog/items";
import { RelatedRecords } from "@/components/catalog/RelatedRecords";
import { messages } from "@/i18n/messages";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function ItemsPage({ params, searchParams }: {
  params: Promise<{ locale: string }>; searchParams: Promise<{ cursor?: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const { cursor } = await searchParams;
  return itemPage(locale, "read", async () => {
    const page = await listItems({ cursor, limit: 20 });
    return <div className="space-y-6"><h1 className="text-3xl font-semibold">{messages[locale].itemDetail}</h1>
      <a className="underline" href={`/${locale}/admin/catalog/instances`}>{messages[locale].instances}</a>
      <RelatedRecords locale={locale} collection="items" references={page.items.map(item => ({ kind: "local", entity_type: "Item", internal_id: item.internal_id, uri: item.uri }))} />
      {page.next_cursor && <a className="underline" href={`/${locale}/admin/catalog/items?cursor=${encodeURIComponent(page.next_cursor)}`}>{messages[locale].nextPage}</a>}
    </div>;
  });
}
