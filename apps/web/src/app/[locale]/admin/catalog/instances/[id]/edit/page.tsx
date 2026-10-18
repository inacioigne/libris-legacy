import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { getInstanceForEditor } from "@/server/catalog/instances";
import { instancePage } from "@/server/catalog/instance-page";
import { reloadInstanceAction, selectWorksAction, updateInstanceAction } from "@/server/catalog/instance-actions";
import { InstanceEditorForm } from "@/components/catalog/InstanceEditorForm";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function EditInstancePage({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  return instancePage(locale, "update", async () => {
    const { instance, etag, metadata } = await getInstanceForEditor(id);
    return <><h1 className="text-3xl font-semibold">{messages[locale].instanceEdit}</h1>
      <InstanceEditorForm locale={locale} mode="edit" instance={instance} instanceId={id} metadata={metadata} ifMatch={etag ?? undefined}
        action={updateInstanceAction} reload={reloadInstanceAction} searchWorks={selectWorksAction} cancelHref={`/${locale}/admin/catalog/instances/${id}`} />
    </>;
  });
}
