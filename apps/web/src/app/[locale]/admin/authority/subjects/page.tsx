import Link from "next/link";
import { notFound } from "next/navigation";
import { isLocale, messages } from "@/i18n/messages";
import { listAuthorities } from "@/server/catalog/authorities";
import { requireAdminPageAccess } from "../../page-access";

export const dynamic = "force-dynamic"; export const runtime = "nodejs";
type Query = Record<string, string | string[] | undefined>;
const first = (query: Query, key: string) => { const value = query[key]; return (Array.isArray(value) ? value[0] : value)?.trim() || undefined; };
export default async function SubjectsPage({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: Promise<Query> }) {
  const { locale } = await params; if (!isLocale(locale)) notFound();
  const access = await requireAdminPageAccess(locale, { mode: "all", requiredPermissions: ["authority:read", "metadata_revision:read"] });
  const query = await searchParams, filters = { internal_id: first(query, "internal_id"), uri: first(query, "uri"), cursor: first(query, "cursor") };
  const result = await listAuthorities("Subject", filters), t = messages[locale], path = `/${locale}/admin/authority/subjects`;
  const next = new URLSearchParams(); if (filters.internal_id) next.set("internal_id", filters.internal_id); if (filters.uri) next.set("uri", filters.uri); if (result.next_cursor) next.set("cursor", result.next_cursor);
  return <main className="mx-auto max-w-7xl px-6 py-10"><div className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-3xl font-semibold">{t.subjectListTitle}</h1><p className="mt-3">{t.subjectListDescription}</p></div><div className="flex gap-3">{access.identity.permissions.includes("authority:manage") && <Link className="rounded-lg bg-teal-700 px-4 py-2 text-white" href={`${path}/new`}>{t.subjectCreateTitle}</Link>}<Link className="rounded-lg border px-4 py-2" href={`/${locale}/admin/authority`}>{t.authorityAgent}</Link></div></div>
    <form className="mt-8 grid gap-4 rounded-2xl border p-5 sm:grid-cols-2"><label>UUID<input className="block w-full rounded border p-2" name="internal_id" defaultValue={filters.internal_id} /></label><label>URI<input className="block w-full rounded border p-2" name="uri" defaultValue={filters.uri} /></label><button className="rounded bg-teal-700 px-4 py-2 text-white" type="submit">{t.agentListFilter}</button></form>
    {!result.items.length ? <p className="mt-8 rounded border p-6">{t.subjectListEmpty}</p> : <div className="mt-8 overflow-x-auto rounded-2xl border"><table className="min-w-full text-left"><thead><tr><th className="p-3">{t.subjectPreferredLabel}</th><th className="p-3">{t.subjectSchemes}</th><th className="p-3">UUID</th><th className="p-3">URI</th></tr></thead><tbody>{result.items.map(item => "preferred_label" in item && <tr className="border-t" key={item.internal_id}><th className="p-3"><Link className="hover:underline" href={`${path}/${item.internal_id}`}>{item.preferred_label.map(label => label.value).join(" / ")}</Link></th><td className="p-3">{item.scheme.map(value => value.uri).join(", ")}</td><td className="p-3 font-mono text-xs">{item.internal_id}</td><td className="p-3 break-all text-sm">{item.uri}</td></tr>)}</tbody></table></div>}
    {result.next_cursor && <Link className="mt-6 inline-block rounded bg-teal-700 px-4 py-2 text-white" href={`${path}?${next}`}>{t.nextPage}</Link>}
  </main>;
}
