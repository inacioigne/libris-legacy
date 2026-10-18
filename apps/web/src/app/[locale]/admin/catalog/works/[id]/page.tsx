import { AuthorityPanel } from "@/server/catalog/authority-panel";
import { relatedChildren } from "@/server/catalog/related";
import { RelatedRecords } from "@/components/catalog/RelatedRecords";
import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { adminNavigation } from "@/server/admin/navigation";
import { guardAdminContent } from "@/server/admin/access";
import { AdminAccessState, AdminFrame, AdminNavigation } from "@/server/admin/shell";
import { isLocale, messages } from "@/i18n/messages";
import { requestIdentity } from "@/server/auth/request";
import { getWork, type WorkRead } from "@/server/catalog";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function titleForWork(work: WorkRead): string {
  return work.title
    .flatMap((entry) => entry.main_title ?? [])
    .map((text) => text.value)
    .filter(Boolean)
    .join(" / ") || "Untitled work";
}

function summarizeReferences(source?: unknown[]): string {
  const items = (source ?? [])
    .flatMap((entry) => {
      if (!entry || typeof entry !== "object") return [];
      const ref = entry as Record<string, unknown>;
      const value = [ref.uri, ref.internal_id, ref.label, ref.namespace]
        .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
        .at(0);
      return value ? [value] : [];
    });
  return items.length > 0 ? items.slice(0, 4).join(", ") : "—";
}

export default async function WorkDetailPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();

  const page: ReactNode = await guardAdminContent(
    requestIdentity,
    async (access): Promise<ReactNode> => {
      try {
        const work = await getWork(id);
        const instances = await relatedChildren("works", id);
        const t = messages[locale];

        return (
          <AdminFrame locale={locale}>
            {/* <div className="grid gap-8 md:grid-cols-[16rem_minmax(0,1fr)]"> */}
              {/* <AdminNavigation locale={locale} items={adminNavigation(access.identity.permissions, locale)} /> */}
              <div className="min-w-0">
                <div className="mb-6 flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.1em] text-teal-800 dark:text-teal-300">{t.workDetail}</p>
                    <h1 className="mt-2 text-3xl font-semibold">{titleForWork(work)}</h1>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    {access.identity.permissions.includes("bibliographic:create") && <Link href={`/${locale}/admin/catalog/instances/new?work=${id}`} className="rounded-lg border px-4 py-2">{t.instanceNew}</Link>}
                    <Link href={`/${locale}/admin/catalog/instances`} className="rounded-lg border px-4 py-2">{t.instances}</Link>
                    {access.identity.permissions.includes("bibliographic:update") && <Link href={`/${locale}/admin/catalog/works/${id}/edit`} className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800">
                      {t.workEditTitle}
                    </Link>}
                    <Link href={`/${locale}/admin/catalog/works`} className="rounded-lg border border-slate-300 px-4 py-2 font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200">
                      {t.backToWorks}
                    </Link>
                  </div>
                </div>

                <dl className="grid gap-5 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900 md:grid-cols-2">
                  <div className="space-y-1">
                    <dt className="text-sm font-medium text-slate-500 dark:text-slate-400">{t.workTitle}</dt>
                    <dd className="text-lg font-medium">{titleForWork(work)}</dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-sm font-medium text-slate-500 dark:text-slate-400">{t.workIdentifier}</dt>
                    <dd className="break-all font-mono text-sm">{work.internal_id}</dd>
                  </div>
                  <div className="space-y-1 md:col-span-2">
                    <dt className="text-sm font-medium text-slate-500 dark:text-slate-400">{t.workUri}</dt>
                    <dd className="break-all font-mono text-sm">{work.uri}</dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-sm font-medium text-slate-500 dark:text-slate-400">{t.workContributors}</dt>
                    <dd>{summarizeReferences(work.contribution?.map((entry) => entry.agent) as unknown[])}</dd>
                  </div>
                  <div className="space-y-1">
                    <dt className="text-sm font-medium text-slate-500 dark:text-slate-400">{t.workSubjects}</dt>
                    <dd>{summarizeReferences(work.subject as unknown[])}</dd>
                  </div>
                </dl>
      <AuthorityPanel collection="works" id={id} locale={locale} permissions={access.identity.permissions} />
                <RelatedRecords references={instances} collection="instances" locale={locale} />
              </div>
            {/* </div> */}
          </AdminFrame>
        );
      } catch (error) {
        return (
          <AdminFrame locale={locale}>
            <div className="grid gap-8 md:grid-cols-[16rem_minmax(0,1fr)]">
              <AdminNavigation locale={locale} items={adminNavigation(access.identity.permissions, locale)} />
              <div className="min-w-0 rounded-2xl border border-red-200 bg-white p-6 dark:border-red-900 dark:bg-gray-900">
                <h1 className="text-3xl font-semibold">{messages[locale].workDetail}</h1>
                <p className="mt-4 text-red-700 dark:text-red-300">
                  {error instanceof Error ? error.message : "Work could not be loaded."}
                </p>
                <Link href={`/${locale}/admin/catalog/works`} className="mt-6 inline-block font-semibold text-teal-700 dark:text-teal-300">
                  {messages[locale].backToWorks}
                </Link>
              </div>
            </div>
          </AdminFrame>
        );
      }
    },
    (access): ReactNode => (
      <AdminFrame locale={locale}>
        <AdminAccessState locale={locale} access={access} />
      </AdminFrame>
    ),
    { mode: "all", requiredPermissions: ["metadata_revision:read"] },
  );

  return page;
}
