import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { getInstance } from "@/server/catalog/instances";
import { itemPage } from "@/server/catalog/item-page";
import { createItemAction, validateItemAction } from "@/server/catalog/item-actions";
import { ItemEditorForm } from "@/components/catalog/ItemEditorForm";
import { instanceTitle } from "@/components/catalog/InstanceViews";
import { itemProfile } from "@/catalog/item-editor";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function NewItemPage({ params, searchParams }: {
  params: Promise<{ locale: string }>; searchParams: Promise<{ instance?: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const { instance: id } = await searchParams, t = messages[locale];
  return itemPage(locale, "create", async () => {
    if (typeof id !== "string" || !id) return <><h1>{t.itemNew}</h1><p>{t.itemRequired}</p><a className="underline" href={`/${locale}/admin/catalog/instances`}>{t.instances}</a></>;
    const { instance } = await getInstance(id);
    return <><h1 className="text-3xl font-semibold">{t.itemNew}</h1><p>{t.workEditorProfile}: {itemProfile}</p>
      <p>{t.itemInstance}: <a className="underline" href={`/${locale}/admin/catalog/instances/${instance.internal_id}`}>{instanceTitle(instance, locale)}</a></p>
      <ItemEditorForm locale={locale} mode="create" action={createItemAction} validate={validateItemAction} initialInstance={instance} cancelHref={`/${locale}/admin/catalog/instances/${instance.internal_id}`} />
    </>;
  });
}
