import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { adminNavigation } from "@/server/admin/navigation";
import { guardAdminContent } from "@/server/admin/access";
import { AdminAccessState, AdminFrame, AdminNavigation } from "@/server/admin/shell";
import { messages, isLocale, type Locale } from "@/i18n/messages";
import { requestIdentity } from "@/server/auth/request";
import { listWorks, type WorkRead } from "@/server/catalog";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type SearchParams = Record<string, string | string[] | undefined>;

function getFirstValue(search: SearchParams, key: string): string | undefined {
  const value = search[key];
  return Array.isArray(value) ? value[0] : value;
}

function buildWorkHref(locale: Locale, search: SearchParams, override: Partial<Record<string, string | undefined>> = {}): string {
  const params = new URLSearchParams();
  for (const key of ["cursor", "internal_id", "uri", "profile", "editorial_state", "limit"] as const) {
    const value = override[key] ?? getFirstValue(search, key);
    if (value && value.trim()) params.set(key, value.trim());
  }
  const query = params.toString();
  return `/${locale}/admin/catalog/works${query ? `?${query}` : ""}`;
}

function titleForWork(work: WorkRead): string {
  return work.title
    .flatMap((entry) => entry.main_title ?? [])
    .map((text) => text.value)
    .filter(Boolean)
    .join(" / ") || "Untitled work";
}

function summarizeReferences(source?: unknown[]): string[] {
  return (source ?? [])
    .flatMap((entry) => {
      if (!entry || typeof entry !== "object") return [];
      const ref = entry as Record<string, unknown>;
      const text = [ref.uri, ref.internal_id, ref.label, ref.namespace]
        .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
        .at(0);
      return text ? [text] : [];
    })
    .slice(0, 3);
}

function summarizeAgents(work: WorkRead): string {
  const agents = summarizeReferences(
    work.contribution?.map((entry) => entry.agent) as unknown[],
  );
  return agents.length > 0 ? agents.join(", ") : "—";
}

function summarizeSubjects(work: WorkRead): string {
  const subjects = summarizeReferences(work.subject as unknown[]);
  return subjects.length > 0 ? subjects.join(", ") : "—";
}

export default async function CatalogWorksPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const query = await searchParams;
  const filters = {
    limit: Math.min(Math.max(Number.parseInt(getFirstValue(query, "limit") ?? "20", 10) || 20, 1), 100),
    cursor: getFirstValue(query, "cursor"),
    internal_id: getFirstValue(query, "internal_id"),
    uri: getFirstValue(query, "uri"),
    profile: getFirstValue(query, "profile"),
    editorial_state: getFirstValue(query, "editorial_state") as "draft" | "in_review" | "approved" | "published" | "withdrawn" | "archived" | undefined,
  };

  const page: ReactNode = await guardAdminContent(
    requestIdentity,
    async (access): Promise<ReactNode> => {
      let result: Awaited<ReturnType<typeof listWorks>>;
      try {
        result = await listWorks(filters);
      } catch (error) {
        return (
          <AdminFrame locale={locale}>
            {/* <div className="grid gap-8 md:grid-cols-[16rem_minmax(0,1fr)]"> */}
              {/* <AdminNavigation locale={locale} items={adminNavigation(access.identity.permissions, locale)} /> */}
              <div className="min-w-0 rounded-2xl border border-red-200 bg-white p-6 dark:border-red-900 dark:bg-gray-900">
                <h1 className="text-3xl font-semibold">{messages[locale].workList}</h1>
                <p className="mt-4 text-red-700 dark:text-red-300">
                  {error instanceof Error ? error.message : "Catalog request failed."}
                </p>
                <Link href={`/${locale}/admin/catalog/works`} className="mt-6 inline-block font-semibold text-teal-700 dark:text-teal-300">
                  {messages[locale].retry}
                </Link>
              </div>
            {/* </div> */}
          </AdminFrame>
        );
      }

      return (
        <AdminFrame locale={locale}>
          {/* <div className="grid gap-8 md:grid-cols-[16rem_minmax(0,1fr)]"> */}
            {/* <AdminNavigation locale={locale} items={adminNavigation(access.identity.permissions, locale)} /> */}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h1 className="text-3xl font-semibold sm:text-4xl">{messages[locale].workList}</h1>
                  <p className="mt-4 max-w-3xl text-slate-700 dark:text-slate-300">
                    {messages[locale].adminDescription}
                  </p>
                </div>
                <Link href={`/${locale}/admin/catalog/works/new`} className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800">
                  {messages[locale].workCreateTitle}
                </Link>
              </div>

              <form method="get" action={`/${locale}/admin/catalog/works`} className="mt-8 grid gap-4 rounded-2xl border border-slate-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900 md:grid-cols-2 xl:grid-cols-5">
                <label className="flex flex-col gap-2 text-sm font-medium">
                  <span>{messages[locale].workIdentifier}</span>
                  <input name="internal_id" defaultValue={filters.internal_id ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950" />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium">
                  <span>{messages[locale].workUri}</span>
                  <input name="uri" defaultValue={filters.uri ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950" />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium">
                  <span>{messages[locale].workState}</span>
                  <select name="editorial_state" defaultValue={filters.editorial_state ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950">
                    <option value="">All</option>
                    {(["draft", "in_review", "approved", "published", "withdrawn", "archived"] as const).map((value) => (
                      <option key={value} value={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium">
                  <span>Limit</span>
                  <select name="limit" defaultValue={String(filters.limit)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950">
                    {[10, 20, 50, 100].map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium">
                  <span>Profile</span>
                  <input name="profile" defaultValue={filters.profile ?? ""} className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950" />
                </label>
                <div className="md:col-span-2 xl:col-span-5 flex flex-wrap gap-3">
                  <button type="submit" className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800">{messages[locale].filterWorks}</button>
                  <Link href={`/${locale}/admin/catalog/works`} className="rounded-lg border border-slate-300 px-4 py-2 font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200">{messages[locale].resetFilters}</Link>
                </div>
              </form>

              {result.items.length === 0 ? (
                <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 text-slate-700 dark:border-gray-700 dark:bg-gray-900 dark:text-slate-200">
                  {messages[locale].noWorks}
                </div>
              ) : (
                <>
                  <div className="mt-8 overflow-x-auto rounded-2xl border border-slate-200 bg-white dark:border-gray-700 dark:bg-gray-900">
                    <table className="min-w-full text-left text-sm">
                      <thead className="bg-slate-50 dark:bg-gray-800">
                        <tr>
                          <th className="px-4 py-3 font-semibold">{messages[locale].workTitle}</th>
                          <th className="px-4 py-3 font-semibold">{messages[locale].workIdentifier}</th>
                          <th className="px-4 py-3 font-semibold">{messages[locale].workUri}</th>
                          <th className="px-4 py-3 font-semibold">{messages[locale].workContributors}</th>
                          <th className="px-4 py-3 font-semibold">{messages[locale].workSubjects}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.items.map((work) => (
                          <tr key={work.internal_id} className="border-t border-slate-200 dark:border-gray-700">
                            <td className="px-4 py-3">
                              <Link href={`/${locale}/admin/catalog/works/${work.internal_id}`} className="font-medium text-teal-700 hover:underline dark:text-teal-300">
                                {titleForWork(work)}
                              </Link>
                            </td>
                            <td className="px-4 py-3 font-mono text-xs">{work.internal_id}</td>
                            <td className="px-4 py-3 break-all text-xs text-slate-600 dark:text-slate-300">{work.uri}</td>
                            <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{summarizeAgents(work)}</td>
                            <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{summarizeSubjects(work)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {result.next_cursor && (
                    <div className="mt-6 flex justify-end">
                      <Link href={buildWorkHref(locale, query, { cursor: result.next_cursor })} className="rounded-lg bg-slate-900 px-4 py-2 font-medium text-white dark:bg-slate-100 dark:text-slate-900">
                        {messages[locale].nextPage}
                      </Link>
                    </div>
                  )}
                </>
              )}
            {/* </div> */}
          </div>
        </AdminFrame>
      );
    },
    (access): ReactNode => (
      <AdminFrame locale={locale}>
        <AdminAccessState locale={locale} access={access} />
      </AdminFrame>
    ),
    { mode: "any", requiredPermissions: ["bibliographic:create", "bibliographic:update", "bibliographic:delete", "metadata_revision:create"] },
  );

  return page;
}
