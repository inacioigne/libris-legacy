import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { isLocale, messages, type Locale } from "@/i18n/messages";
import { requestIdentity } from "@/server/auth/request";
import { guardAdminContent } from "@/server/admin/access";
import { AdminAccessState, AdminFrame, AdminShell } from "@/server/admin/shell";
import { requireAdminPageAccess } from "./page-access";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function AdminPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: Locale }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { locale } = await params;
  const t = messages[locale];
  if (!isLocale(locale)) notFound();
  const query = await searchParams;
  const feedback =
    query.auth === "cancelled"
      ? messages[locale].cancelled
      : query.auth === "error"
        ? messages[locale].authError
        : undefined;

  await requireAdminPageAccess(locale, {
      mode: "all",
      requiredPermissions: ["system:admin"],
    });

  return (
    <main className="mx-auto grid max-w-7xl grid-cols-1 gap-6 px-6 py-10 sm:grid-cols-2">
      <Link href={`/${locale}/admin/catalog/works/new`}>
        <div className="scroll-mt-6 rounded-2xl border border-slate-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
          <h3 className="text-xl font-semibold">{t.cataloging}</h3>
        </div>
      </Link>
      <Link href={`/${locale}/admin/authority`}>
        <div className="scroll-mt-6 rounded-2xl border border-slate-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
          <h3 className="text-xl font-semibold">{t.authorityAgent}</h3>
        </div>
      </Link>
    </main>
  );
}
