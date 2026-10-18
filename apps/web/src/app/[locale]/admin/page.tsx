import type { ReactNode } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { isLocale, messages, type Locale } from "@/i18n/messages";
import { requestIdentity } from "@/server/auth/request";
import { guardAdminContent } from "@/server/admin/access";
import { AdminAccessState, AdminFrame, AdminShell } from "@/server/admin/shell";

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

  return (
    <main className="mx-auto max-w-7xl py-10">
      <Link href={`/${locale}/admin/catalog/works/new`}>
        <div className="scroll-mt-6 rounded-2xl border border-slate-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6">
          <h3 className="text-xl font-semibold">{t.cataloging}</h3>
        </div>
      </Link>
    </main>
  );

  // const content = await guardAdminContent<ReactNode>(
  //   requestIdentity,
  //   (access) => (
  //     <>
  //       <AdminShell locale={locale} access={access} />
  //       {access.identity.permissions.includes("bibliographic:create") && (
  //         <section
  //           aria-labelledby="works-card-title"
  //           className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900"
  //         >
  //           <div className="flex flex-wrap items-center justify-between gap-4">
  //             <h2 id="works-card-title" className="text-xl font-semibold">
  //               {messages[locale].works}
  //             </h2>
  //             <Link
  //               href={`/${locale}/admin/catalog/works/new`}
  //               className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800"
  //             >
  //               {messages[locale].workCreateTitle}
  //             </Link>
  //           </div>
  //         </section>
  //       )}
  //     </>
  //   ),
  //   (access) => (
  //     <AdminAccessState locale={locale} access={access} feedback={feedback} />
  //   ),
  // );

  // return <AdminFrame locale={locale}>{content}</AdminFrame>;
}
