import Link from "next/link";
import { notFound } from "next/navigation";
import { SubjectEditorForm } from "@/components/catalog/SubjectEditorForm";
import { subjectCanRoundTrip } from "@/catalog/subject-editor";
import { isLocale, messages } from "@/i18n/messages";
import { AdminFrame } from "@/server/admin/shell";
import { getSubjectForEditor } from "@/server/catalog/authorities";
import { reloadSubjectAction, updateSubjectAction } from "@/server/catalog/subject-actions";
import { requireAdminPageAccess } from "../../../../page-access";
export const dynamic = "force-dynamic"; export const runtime = "nodejs";
export default async function EditSubjectPage({ params }: { params: Promise<{ locale: string; id: string }> }) { const { locale, id } = await params; if (!isLocale(locale)) notFound(); await requireAdminPageAccess(locale, { mode: "all", requiredPermissions: ["authority:manage", "authority:read", "metadata_revision:read"] }); const { subject, etag } = await getSubjectForEditor(id); const t = messages[locale]; return <AdminFrame locale={locale}><main><h1 className="text-3xl font-semibold">{t.subjectEditTitle}</h1>{subjectCanRoundTrip(subject) ? <SubjectEditorForm locale={locale} action={updateSubjectAction} subject={subject} ifMatch={etag ?? undefined} reload={reloadSubjectAction} /> : <div role="alert" className="mt-8 rounded border border-amber-500 p-6"><p>{t.agentEditBlocked}</p><Link className="underline" href={`/${locale}/admin/authority/subjects/${id}`}>{t.workFormCancel}</Link></div>}</main></AdminFrame>; }
