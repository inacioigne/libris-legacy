import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { getItemForEditor } from "@/server/catalog/items";
import { itemPage } from "@/server/catalog/item-page";
import { reloadItemAction, updateItemAction } from "@/server/catalog/item-actions";
import { ItemEditorForm } from "@/components/catalog/ItemEditorForm";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function EditItemPage({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  return itemPage(locale, "update", async () => {
    const { item, etag, metadata } = await getItemForEditor(id);
    return <><h1 className="text-3xl font-semibold">{messages[locale].itemEdit}</h1>
      <ItemEditorForm locale={locale} mode="edit" item={item} itemId={id} metadata={metadata} ifMatch={etag ?? undefined}
        action={updateItemAction} reload={reloadItemAction} cancelHref={`/${locale}/admin/catalog/items/${id}`} />
    </>;
  });
}
