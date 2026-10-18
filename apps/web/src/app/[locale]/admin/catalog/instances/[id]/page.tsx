import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { getInstanceForEditor, resolveInstanceWork } from "@/server/catalog/instances";
import { instancePage } from "@/server/catalog/instance-page";
import { InstanceDescription, instanceTitle } from "@/components/catalog/InstanceViews";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function InstanceDetailPage({ params, searchParams }: {
  params: Promise<{ locale: string; id: string }>; searchParams: Promise<{ saved?: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  const t = messages[locale], { saved } = await searchParams;
  return instancePage(locale, "read", async identity => {
    const envelope = await getInstanceForEditor(id);
    const work = await resolveInstanceWork(envelope.instance.instance_of).catch(() => undefined);
    return <div className="space-y-6">
      <p>{t.instanceDetail}</p><h1 className="text-3xl font-semibold">{instanceTitle(envelope.instance, locale)}</h1>
      {saved === "1" && <p role="status">{t.instanceSaved}</p>}
      <div className="flex flex-wrap gap-4"><a className="underline" href={`/${locale}/admin/catalog/instances`}>{t.instances}</a>
        {identity.permissions.includes("bibliographic:update") && <a className="underline" href={`/${locale}/admin/catalog/instances/${id}/edit`}>{t.instanceEdit}</a>}
      </div>
      <section className="rounded-xl border p-4"><h2 className="text-xl font-semibold">{t.instanceWork}</h2>
        {work ? <><a className="underline" href={`/${locale}/admin/catalog/works/${work.internal_id}`}>{work.title.flatMap(title => title.main_title.map(text => text.value)).join(" / ")}</a><p className="break-all">{work.internal_id}<br />{work.uri}</p></> : <p>{t.instanceWorkUnavailable}</p>}
      </section>
      <InstanceDescription envelope={envelope} locale={locale} />
    </div>;
  });
}
