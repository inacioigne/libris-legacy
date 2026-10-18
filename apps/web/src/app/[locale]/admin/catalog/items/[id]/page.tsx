import { AuthorityPanel } from "@/server/catalog/authority-panel";
import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { getItemForEditor } from "@/server/catalog/items";
import { resolveItemInstance } from "@/server/catalog/related";
import { resolveInstanceWork } from "@/server/catalog/instances";
import { itemPage } from "@/server/catalog/item-page";
import { ItemDescription } from "@/components/catalog/ItemViews";
import { instanceTitle } from "@/components/catalog/InstanceViews";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function ItemDetailPage({ params, searchParams }: {
  params: Promise<{ locale: string; id: string }>; searchParams: Promise<{ saved?: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  const t = messages[locale], { saved } = await searchParams;
  return itemPage(locale, "read", async identity => {
    const envelope = await getItemForEditor(id);
    const instance = await resolveItemInstance(envelope.item.item_of).catch(() => undefined);
    const work = instance ? await resolveInstanceWork(instance.instance_of).catch(() => undefined) : undefined;
    return <div className="space-y-6"><h1 className="text-3xl font-semibold">{t.itemDetail}</h1>
      {saved === "1" && <p role="status">{t.itemSaved}</p>}
      <nav className="flex flex-wrap gap-4" aria-label={t.itemInstance}>
        {instance && <a className="underline" href={`/${locale}/admin/catalog/instances/${instance.internal_id}`}>{t.itemInstance}: {instanceTitle(instance, locale)}</a>}
        {work && <a className="underline" href={`/${locale}/admin/catalog/works/${work.internal_id}`}>{t.instanceWork}</a>}
        {identity.permissions.includes("bibliographic:update") && <a className="underline" href={`/${locale}/admin/catalog/items/${id}/edit`}>{t.itemEdit}</a>}
      </nav>
      {!instance && <p>{t.itemInstanceUnavailable}</p>}
      <section><h2>{t.itemInstance}</h2><pre className="whitespace-pre-wrap break-all">{JSON.stringify(envelope.item.item_of, null, 2)}</pre></section>
      <AuthorityPanel collection="items" id={id} locale={locale} permissions={identity.permissions} />
      <ItemDescription envelope={envelope} locale={locale} />
    </div>;
  });
}
