import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { getWork } from "@/server/catalog";
import { instancePage } from "@/server/catalog/instance-page";
import { createInstanceAction, selectWorksAction, validateInstanceAction } from "@/server/catalog/instance-actions";
import { InstanceEditorForm } from "@/components/catalog/InstanceEditorForm";
import { instanceProfile } from "@/catalog/instance-editor";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function NewInstancePage({ params, searchParams }: {
  params: Promise<{ locale: string }>; searchParams: Promise<{ work?: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const { work } = await searchParams;
  return instancePage(locale, "create", async () => {
    const initialWork = typeof work === "string" ? await getWork(work) : undefined;
    return <><h1 className="text-3xl font-semibold">{messages[locale].instanceNew}</h1>
      <p>{messages[locale].workEditorProfile}: {instanceProfile}</p>
      {initialWork && <p>{messages[locale].instanceWork}: <a className="underline" href={`/${locale}/admin/catalog/works/${initialWork.internal_id}`}>{initialWork.title.flatMap(title => title.main_title.map(text => text.value)).join(" / ")}</a></p>}
      <InstanceEditorForm locale={locale} mode="create" action={createInstanceAction} searchWorks={selectWorksAction} validate={validateInstanceAction} initialWork={initialWork} cancelHref={`/${locale}/admin/catalog/instances`} />
    </>;
  });
}
