import Link from "next/link";
import { notFound } from "next/navigation";
import { EditorialPanel } from "@/components/catalog/EditorialPanel";
import { isLocale, messages } from "@/i18n/messages";
import { AdminFrame } from "@/server/admin/shell";
import { getAgentForEditor } from "@/server/catalog/authorities";
import { getEditorialDashboard } from "@/server/catalog/editorial";
import { requireAdminPageAccess } from "../../page-access";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function references(values: { uri: string }[] | undefined) {
  if (!values?.length) return <p>—</p>;
  return <ul className="space-y-1">{values.map((value, index) => <li className="break-all font-mono text-sm" key={`${value.uri}-${index}`}>{value.uri}</li>)}</ul>;
}

export default async function AgentDetailPage({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  const access = await requireAdminPageAccess(locale, { mode: "all", requiredPermissions: ["authority:read", "metadata_revision:read"] });
  const [{ agent }, editorial] = await Promise.all([
    getAgentForEditor(id), getEditorialDashboard("agents", id, access.identity.permissions),
  ]);
  const t = messages[locale];
  return <AdminFrame locale={locale}><main id="content" tabIndex={-1} className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm font-semibold uppercase tracking-wider text-teal-700">{t.agentDetailTitle}</p>
      <h1 className="mt-2 text-3xl font-semibold">{agent.authorized_name.map(name => name.value).join(" / ")}</h1></div>
      <nav className="flex flex-wrap gap-3" aria-label={t.agentDetailTitle}>
        {access.identity.permissions.includes("authority:manage") && <Link className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white" href={`/${locale}/admin/authority/${id}/edit`}>{t.agentEditTitle}</Link>}
        <Link className="rounded-lg border px-4 py-2" href={`/${locale}/admin/authority`}>{t.agentBackToList}</Link>
      </nav></div>
    <dl className="grid gap-5 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900 md:grid-cols-2">
      <div><dt className="font-semibold">{t.agentKind}</dt><dd>{agent.kind}</dd></div>
      <div><dt className="font-semibold">UUID</dt><dd className="break-all font-mono text-sm">{agent.internal_id}</dd></div>
      <div className="md:col-span-2"><dt className="font-semibold">URI</dt><dd className="break-all font-mono text-sm">{agent.uri}</dd></div>
      <div><dt className="font-semibold">{t.agentAuthorizedName}</dt><dd>{agent.authorized_name.map((name, index) => <span className="block" lang={name.language ?? undefined} key={index}>{name.value}{name.language ? ` (${name.language})` : ""}</span>)}</dd></div>
      <div><dt className="font-semibold">{t.agentVariantNames}</dt><dd>{agent.variant_name?.length ? agent.variant_name.map((name, index) => <span className="block" lang={name.language ?? undefined} key={index}>{name.value}{name.language ? ` (${name.language})` : ""}</span>) : "—"}</dd></div>
      <div className="md:col-span-2"><dt className="font-semibold">{t.agentIdentifiers}</dt><dd>{agent.identified_by?.length ? <ul className="space-y-2">{agent.identified_by.map((identifier, index) => <li key={index}><span>{identifier.kind}: {identifier.value}</span>{identifier.source?.length ? <ul>{identifier.source.map((source, sourceIndex) => <li className="break-all font-mono text-sm" key={sourceIndex}>{source.uri}</li>)}</ul> : null}</li>)}</ul> : "—"}</dd></div>
      <div><dt className="font-semibold">{t.agentExternalReferences}</dt><dd>{references(agent.external_reference)}</dd></div>
      <div><dt className="font-semibold">{t.agentProvenance}</dt><dd>{references(agent.provenance)}</dd></div>
      <div className="md:col-span-2"><dt className="font-semibold">{t.agentRelatedAuthorities}</dt><dd>{agent.related_authority?.length ? <pre className="overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify(agent.related_authority, null, 2)}</pre> : "—"}</dd></div>
    </dl>
    <EditorialPanel collection="agents" id={id} locale={locale} permissions={access.identity.permissions} initial={editorial} />
  </main></AdminFrame>;
}
