import { notFound } from "next/navigation";
import { AgentCreateForm } from "@/components/catalog/AgentCreateForm";
import { isLocale, messages } from "@/i18n/messages";
import { createAgentAction } from "@/server/catalog/agent-actions";
import { requireAdminPageAccess } from "../../page-access";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function NewAgentPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const access = await requireAdminPageAccess(locale, { mode: "all", requiredPermissions: ["authority:manage"] });
  const canBrowse = access.identity.permissions.includes("authority:read") && access.identity.permissions.includes("metadata_revision:read");
  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl px-6 py-10">
      <h1 className="text-3xl font-semibold">{messages[locale].agentCreateTitle}</h1>
      <p className="mt-4 text-slate-700 dark:text-slate-300">{messages[locale].agentCreateDescription}</p>
      <AgentCreateForm locale={locale} action={createAgentAction} canBrowse={canBrowse} />
    </main>
  );
}
