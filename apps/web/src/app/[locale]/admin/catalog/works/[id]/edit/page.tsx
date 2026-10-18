import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { WorkEditorForm } from "@/components/catalog/WorkEditorForm";
import { messages, isLocale, type Locale } from "@/i18n/messages";
import { guardAdminContent } from "@/server/admin/access";
import { adminNavigation } from "@/server/admin/navigation";
import { AdminAccessState, AdminFrame, AdminNavigation } from "@/server/admin/shell";
import { requestIdentity } from "@/server/auth/request";
import { getWorkForEditor } from "@/server/catalog";
import { reloadWorkAction, updateWorkAction } from "@/server/catalog/actions";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function EditWorkPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();

  const page: ReactNode = await guardAdminContent(
    requestIdentity,
    async (access): Promise<ReactNode> => {
      const t = messages[locale];
      try {
        const { work, etag, metadata } = await getWorkForEditor(id);
        return (
          <AdminFrame locale={locale}>
            <div className="grid gap-8 md:grid-cols-[16rem_minmax(0,1fr)]">
              <AdminNavigation locale={locale} items={adminNavigation(access.identity.permissions, locale)} />
              <div className="min-w-0">
                <div className="mb-6 flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.1em] text-teal-800 dark:text-teal-300">{t.cataloging}</p>
                    <h1 className="mt-2 text-3xl font-semibold">{t.workEditTitle}</h1>
                  </div>
                  <Link href={`/${locale}/admin/catalog/works/${id}`} className="rounded-lg border border-slate-300 px-4 py-2 font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200">
                    {t.backToWorks}
                  </Link>
                </div>
                <WorkEditorForm
                  locale={locale as Locale}
                  mode="edit"
                  action={updateWorkAction}
                  reload={reloadWorkAction}
                  work={work}
                  metadata={metadata}
                  workId={id}
                  ifMatch={etag ?? undefined}
                  cancelHref={`/${locale}/admin/catalog/works/${id}`}
                />
              </div>
            </div>
          </AdminFrame>
        );
      } catch (error) {
        return (
          <AdminFrame locale={locale}>
            <div className="grid gap-8 md:grid-cols-[16rem_minmax(0,1fr)]">
              <AdminNavigation locale={locale} items={adminNavigation(access.identity.permissions, locale)} />
              <div className="min-w-0 rounded-2xl border border-red-200 bg-white p-6 dark:border-red-900 dark:bg-gray-900">
                <h1 className="text-3xl font-semibold">{t.workEditTitle}</h1>
                <p className="mt-4 text-red-700 dark:text-red-300">
                  {t.workEditorLoadFailed}
                </p>
                <Link href={`/${locale}/admin/catalog/works`} className="mt-6 inline-block font-semibold text-teal-700 dark:text-teal-300">
                  {t.backToWorks}
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
    { mode: "any", requiredPermissions: ["bibliographic:update", "metadata_revision:create"] },
  );

  return page;
}
