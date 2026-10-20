import type { ReactNode } from "react";
import { notFound } from "next/navigation";
import { WorkEditorForm } from "@/components/catalog/WorkEditorForm";
import { messages, isLocale, type Locale } from "@/i18n/messages";
import { guardAdminContent } from "@/server/admin/access";
// import { adminNavigation } from "@/server/admin/navigation";
import {
  AdminAccessState,
  AdminFrame,
  // AdminNavigation,
} from "@/server/admin/shell";
import { requestIdentity } from "@/server/auth/request";
import { createWorkAction, validateWorkAction } from "@/server/catalog/actions";
import {
  resolveVocabularyConceptAction,
  searchVocabularyConceptsAction,
} from "@/server/catalog/vocabulary-actions";
import { requireAdminPageAccess } from "../../../page-access";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function NewWorkPage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = messages[locale];
  if (!isLocale(locale)) notFound();

  // const page: ReactNode = await guardAdminContent(
  //   requestIdentity,
  //   (access): ReactNode => {
  //     const t = messages[locale];
  //     return (
  //       <AdminFrame locale={locale}>
  //         <div className="min-w-0">
  //           <div className="mb-6 flex items-center justify-between gap-4">
  //             <div>
  //               <p className="text-sm font-semibold uppercase text-teal-800 dark:text-teal-300">
  //                 {t.cataloging}
  //               </p>
  //               <h1 className="mt-2 text-3xl font-semibold">
  //                 {t.workCreateTitle}
  //               </h1>
  //             </div>
  //           </div>
  //           <WorkEditorForm
  //             locale={locale as Locale}
  //             mode="create"
  //             action={createWorkAction}
  //             validate={validateWorkAction}
  //             searchVocabulary={searchVocabularyConceptsAction}
  //             resolveVocabulary={resolveVocabularyConceptAction}
  //             cancelHref={`/${locale}/admin/catalog/works`}
  //           />
  //         </div>
  //         {/* </div> */}
  //       </AdminFrame>
  //     );
  //   },
  //   (access): ReactNode => (
  //     <AdminFrame locale={locale}>
  //       <AdminAccessState locale={locale} access={access} />
  //     </AdminFrame>
  //   ),
  //   {
  //     mode: "any",
  //     requiredPermissions: [
  //       "bibliographic:create",
  //       "bibliographic:update",
  //       "metadata_revision:create",
  //     ],
  //   },
  // );

  // return page;
  await requireAdminPageAccess(locale, {
    mode: "all",
    requiredPermissions: ["bibliographic:create"],
  });

  return (
    <main id="content" tabIndex={-1} className="mx-auto max-w-7xl px-6 py-10">
      <div className="min-w-0">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-semibold uppercase text-teal-800 dark:text-teal-300">
              {t.cataloging}
            </p>
            <h1 className="mt-2 text-3xl font-semibold">{t.workCreateTitle}</h1>
          </div>
        </div>
        <WorkEditorForm
          locale={locale as Locale}
          mode="create"
          action={createWorkAction}
          validate={validateWorkAction}
          searchVocabulary={searchVocabularyConceptsAction}
          resolveVocabulary={resolveVocabularyConceptAction}
          cancelHref={`/${locale}/admin/catalog/works`}
        />
      </div>
    </main>
  );
}
