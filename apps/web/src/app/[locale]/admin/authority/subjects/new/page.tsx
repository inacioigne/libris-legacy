import { notFound } from "next/navigation";
import { SubjectEditorForm } from "@/components/catalog/SubjectEditorForm";
import { isLocale, messages } from "@/i18n/messages";
import { createSubjectAction } from "@/server/catalog/subject-actions";
import { requireAdminPageAccess } from "../../../page-access";
export const dynamic = "force-dynamic"; export const runtime = "nodejs";
export default async function NewSubjectPage({ params }: { params: Promise<{ locale: string }> }) { const { locale } = await params; if (!isLocale(locale)) notFound(); const access = await requireAdminPageAccess(locale, { mode: "all", requiredPermissions: ["authority:manage"] }); const t = messages[locale]; const canBrowse = access.identity.permissions.includes("authority:read") && access.identity.permissions.includes("metadata_revision:read"); return <main className="mx-auto max-w-5xl px-6 py-10"><h1 className="text-3xl font-semibold">{t.subjectCreateTitle}</h1><p className="mt-4">{t.subjectCreateDescription}</p><SubjectEditorForm locale={locale} action={createSubjectAction} canBrowse={canBrowse} /></main>; }
