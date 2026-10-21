import { AuthorityPanel } from "@/server/catalog/authority-panel";
import { relatedChildren } from "@/server/catalog/related";
import { RelatedRecords } from "@/components/catalog/RelatedRecords";
import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { guardAdminContent } from "@/server/admin/access";
import { AdminAccessState, AdminFrame } from "@/server/admin/shell";
import { isLocale, messages } from "@/i18n/messages";
import { requestIdentity } from "@/server/auth/request";
import { getWork } from "@/server/catalog";
import { getEditorialDashboard } from "@/server/catalog/editorial";
import { WorkDescription, workTitle } from "@/components/catalog/WorkViews";
import { WorkPublicationControl } from "@/components/catalog/WorkPublicationControl";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

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
        const [work, editorial, instances] = await Promise.all([
          getWork(id),
          getEditorialDashboard("works", id, access.identity.permissions),
          relatedChildren("works", id),
        ]);
        const t = messages[locale];

        return (
          <AdminFrame locale={locale}>
            <div className="min-w-0">
                <div className="mb-6 flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.1em] text-teal-800 dark:text-teal-300">{t.workDetail}</p>
                    <h1 className="mt-2 text-3xl font-semibold">{workTitle(work, locale)}</h1>
                    <div className="mt-3">
                      <WorkPublicationControl
                        id={id}
                        locale={locale}
                        permissions={access.identity.permissions}
                        initial={editorial}
                      />
                    </div>
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

                <WorkDescription work={work} locale={locale} />
                <div className="mt-6 space-y-6">
                  <AuthorityPanel collection="works" id={id} locale={locale} permissions={access.identity.permissions} />
                  <RelatedRecords references={instances} collection="instances" locale={locale} />
                </div>
              </div>
          </AdminFrame>
        );
      } catch {
        return (
          <AdminFrame locale={locale}>
            <div className="min-w-0 rounded-2xl border border-red-200 bg-white p-6 dark:border-red-900 dark:bg-gray-900">
              <h1 className="text-3xl font-semibold">{messages[locale].workDetail}</h1>
              <p className="mt-4 text-red-700 dark:text-red-300">
                {messages[locale].workDetailLoadFailed}
              </p>
              <Link href={`/${locale}/admin/catalog/works`} className="mt-6 inline-block font-semibold text-teal-700 dark:text-teal-300">
                {messages[locale].backToWorks}
              </Link>
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
    {
      mode: "all",
      requiredPermissions: ["bibliographic:read", "metadata_revision:read"],
    },
  );

  return page;
}
