import Link from "next/link";
import { notFound } from "next/navigation";
import { AgentCreateForm } from "@/components/catalog/AgentCreateForm";
import { agentCanRoundTrip } from "@/catalog/agent-editor";
import { isLocale, messages } from "@/i18n/messages";
import { AdminFrame } from "@/server/admin/shell";
import { reloadAgentAction, updateAgentAction } from "@/server/catalog/agent-actions";
import { getAgentForEditor } from "@/server/catalog/authorities";
import { requireAdminPageAccess } from "../../../page-access";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function EditAgentPage({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  await requireAdminPageAccess(locale, { mode: "all", requiredPermissions: ["authority:manage", "authority:read", "metadata_revision:read"] });
  const { agent, etag } = await getAgentForEditor(id);
  const t = messages[locale];
  return <AdminFrame locale={locale}><main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl">
    <h1 className="text-3xl font-semibold">{t.agentEditTitle}</h1><p className="mt-4">{t.agentEditDescription}</p>
    {agentCanRoundTrip(agent) ? <AgentCreateForm locale={locale} action={updateAgentAction} canBrowse agent={agent} ifMatch={etag ?? undefined} reload={reloadAgentAction} />
      : <div role="alert" className="mt-8 rounded-2xl border border-amber-500 p-6"><p>{t.agentEditBlocked}</p><Link className="mt-4 inline-block underline" href={`/${locale}/admin/authority/${id}`}>{t.workFormCancel}</Link></div>}
  </main></AdminFrame>;
}
