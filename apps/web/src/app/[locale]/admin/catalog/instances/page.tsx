import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { listInstances } from "@/server/catalog/instances";
import type { ListWorksQuery } from "@/server/catalog";
import { instancePage } from "@/server/catalog/instance-page";
import { InstanceList } from "@/components/catalog/InstanceViews";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export default async function InstancesPage({ params, searchParams }: {
  params: Promise<{ locale: string }>; searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const search = await searchParams, t = messages[locale];
  const query = Object.fromEntries(["cursor", "internal_id", "uri", "profile", "editorial_state"].map(key => [key, typeof search[key] === "string" ? search[key] : undefined]));
  const limit = Math.min(100, Math.max(1, Number.parseInt(String(search.limit ?? "20"), 10) || 20));
  return instancePage(locale, "read", async identity => {
    const page = await listInstances({ ...query, limit } as ListWorksQuery);
    const next = new URLSearchParams(Object.entries({ ...query, limit: String(limit), cursor: page.next_cursor }).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1] !== ""));
    return <div className="space-y-6">
      <a href={`/${locale}/admin/catalog/works`} className="underline">{t.works}</a>
      <form method="get" className="grid gap-4 rounded-xl border p-4 md:grid-cols-3">
        {([["internal_id", t.workIdentifier], ["uri", t.workUri], ["profile", t.workEditorProfile]] as const).map(([name, label]) => <label key={name}>{label}<input name={name} defaultValue={query[name]} className="block w-full rounded border p-2 dark:bg-gray-950" /></label>)}
        <label>{t.workState}<select name="editorial_state" defaultValue={query.editorial_state} className="block w-full rounded border p-2 dark:bg-gray-950"><option value="">{t.instanceAllStates}</option>{(["draft", "in_review", "approved", "published", "withdrawn", "archived"] as const).map(state => <option key={state} value={state}>{t[`workEditorState_${state}`]}</option>)}</select></label>
        <label>{t.instanceLimit}<input name="limit" type="number" min={1} max={100} defaultValue={limit} className="block w-full rounded border p-2 dark:bg-gray-950" /></label>
        <div className="flex items-end gap-3"><button type="submit" className="rounded border px-3 py-2">{t.instanceFilter}</button><a href={`/${locale}/admin/catalog/instances`} className="underline">{t.resetFilters}</a></div>
      </form>
      <InstanceList page={page} locale={locale} canCreate={identity.permissions.includes("bibliographic:create")} />
      {page.next_cursor && <a href={`/${locale}/admin/catalog/instances?${next}`} className="inline-block rounded border p-3">{t.nextPage}</a>}
    </div>;
  });
}
