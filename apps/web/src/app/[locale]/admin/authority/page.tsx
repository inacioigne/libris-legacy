import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { CatalogAccessError } from "@/server/catalog";
import { listAuthorities } from "@/server/catalog/authorities";
import { requireAdminPageAccess } from "../page-access";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type SearchParams = Record<string, string | string[] | undefined>;

function firstValue(query: SearchParams, key: string): string | undefined {
  const value = query[key];
  return (Array.isArray(value) ? value[0] : value)?.trim() || undefined;
}

export default async function AuthorityPage({ params, searchParams }: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = messages[locale];
  const access = await requireAdminPageAccess(locale, {
    mode: "any", requiredPermissions: ["metadata_revision:read", "authority:manage"],
  });
  if (!access.identity.permissions.includes("authority:read") || !access.identity.permissions.includes("metadata_revision:read")) {
    if (access.identity.permissions.includes("authority:manage")) redirect(`/${locale}/admin/authority/new`);
    redirect(`/${locale}/admin/forbidden`);
  }

  const query = await searchParams;
  const filters = {
    internal_id: firstValue(query, "internal_id"),
    uri: firstValue(query, "uri"),
    cursor: firstValue(query, "cursor"),
  };
  const path = `/${locale}/admin/authority`;
  const pageQuery = new URLSearchParams();
  if (filters.internal_id) pageQuery.set("internal_id", filters.internal_id);
  if (filters.uri) pageQuery.set("uri", filters.uri);
  const retryHref = pageQuery.size ? `${path}?${pageQuery}` : path;

  let result: Awaited<ReturnType<typeof listAuthorities>> | undefined;
  let failure: string | undefined;
  try {
    result = await listAuthorities("Agent", filters);
  } catch (error) {
    if (error instanceof CatalogAccessError) {
      if (error.status === 401) redirect(`/${locale}/admin/login`);
      if (error.status === 403) redirect(`/${locale}/admin/forbidden`);
      if (error.status === 422) failure = t.agentListFilterHelp;
    }
    failure ??= t.agentListUnavailable;
  }
  if (result?.next_cursor) pageQuery.set("cursor", result.next_cursor);

  const kinds = { Agent: t.agentGeneric, Person: t.agentPerson, Organization: t.agentOrganization };
  const inputClass = "rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950";

  return (
    <main className="mx-auto w-full max-w-7xl px-6 py-10">
      <h1 className="text-3xl font-semibold sm:text-4xl">{t.authorityAgent}</h1>
      <p className="mt-4 max-w-3xl text-slate-700 dark:text-slate-300">{t.agentListDescription}</p>
      {access.identity.permissions.includes("authority:manage") && (
        <Link href={`${path}/new`} className="mt-6 inline-block rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800">{t.agentCreateTitle}</Link>
      )}
      <Link href={`${path}/subjects`} className="ml-3 mt-6 inline-block rounded-lg border px-4 py-2 font-medium">{t.authoritySubject}</Link>

      <form method="get" action={path} aria-describedby="agent-filter-help"
        className="mt-8 grid gap-4 rounded-2xl border border-slate-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900 sm:grid-cols-2">
        <label className="flex flex-col gap-2 text-sm font-medium">
          <span>UUID</span>
          <input name="internal_id" defaultValue={filters.internal_id ?? ""} className={inputClass} />
        </label>
        <label className="flex flex-col gap-2 text-sm font-medium">
          <span>URI</span>
          <input name="uri" defaultValue={filters.uri ?? ""} className={inputClass} />
        </label>
        <p id="agent-filter-help" className="text-sm text-slate-600 dark:text-slate-300 sm:col-span-2">{t.agentListFilterHelp}</p>
        <div className="flex flex-wrap gap-3 sm:col-span-2">
          <button type="submit" className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800">{t.agentListFilter}</button>
          <Link href={path} className="rounded-lg border border-slate-300 px-4 py-2 font-medium dark:border-gray-700">{t.resetFilters}</Link>
        </div>
      </form>

      {failure ? (
        <div role="alert" className="mt-8 rounded-2xl border border-red-200 p-6 dark:border-red-900">
          <p>{failure}</p>
          <Link href={retryHref} className="mt-4 inline-block font-semibold text-teal-700 hover:underline dark:text-teal-300">{t.retry}</Link>
        </div>
      ) : result && (
        result.items.length === 0 ? (
          <p className="mt-8 rounded-2xl border border-slate-200 p-6 dark:border-gray-700">{t.agentListEmpty}</p>
        ) : (
          <>
            <div className="mt-8 overflow-x-auto rounded-2xl border border-slate-200 bg-white dark:border-gray-700 dark:bg-gray-900">
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">{t.authorityAgent}</caption>
                <thead className="bg-slate-50 dark:bg-gray-800">
                  <tr>
                    {[t.agentAuthorizedName, t.agentKind, "UUID", "URI"].map(label => (
                      <th key={label} scope="col" className="px-4 py-3 font-semibold">{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.items.map(agent => "authorized_name" in agent && (
                    <tr key={agent.internal_id} className="border-t border-slate-200 dark:border-gray-700">
                      <th scope="row" className="px-4 py-3 font-medium">
                        <Link href={`${path}/${agent.internal_id}`} className="block hover:underline"><ul className="space-y-1">
                          {agent.authorized_name.map((name, index) => (
                            <li key={index} lang={name.language ?? undefined}>{name.value}</li>
                          ))}
                        </ul></Link>
                      </th>
                      <td className="px-4 py-3">{kinds[agent.kind]}</td>
                      <td className="px-4 py-3 font-mono text-xs">{agent.internal_id}</td>
                      <td className="break-all px-4 py-3 text-slate-600 dark:text-slate-300">{agent.uri}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <nav aria-label={t.authorityAgent} className="mt-6 flex flex-wrap justify-between gap-3">
              {filters.cursor && <Link href={retryHref} className="rounded-lg border border-slate-300 px-4 py-2 dark:border-gray-700">{t.agentListFirstPage}</Link>}
              {result.next_cursor && <Link href={`${path}?${pageQuery}`} className="ml-auto rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800">{t.nextPage}</Link>}
            </nav>
          </>
        )
      )}
    </main>
  );
}
