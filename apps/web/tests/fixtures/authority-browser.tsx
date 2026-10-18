import React from "react";
import { createRoot } from "react-dom/client";
import { AuthorityRelations } from "../../src/components/catalog/AuthorityRelations";
import { agent, subject, initialRelations } from "../authority-fixture";
import { authorityLabel, sameLink, selectedLink, type Link, type RelationEnvelope } from "../../src/catalog/authorities";
import { messages } from "../../src/i18n/messages";

declare global {
  interface Window {
    authorityWrites: { op: string; link: Link; etag: string }[];
    authorityStatus: number;
    authoritySearchStatus: number;
    authorityCurrent: RelationEnvelope;
  }
}
const query = new URLSearchParams(location.search), locale = query.get("locale") === "pt" ? "pt" : "en";
const t = messages[locale];
window.authorityWrites = []; window.authorityStatus = 200; window.authoritySearchStatus = 200;
const initial = structuredClone(initialRelations);
const external = selectedLink(initial.relations.source, agent, ["urn:role:legacy"]);
external.target = { kind: "external", entity_type: "Agent", uri: "urn:external:agent" };
const pending = selectedLink(initial.relations.source, subject, []);
pending.target = { kind: "unreconciled", entity_type: "Subject", namespace: "import", key: "pending", label: "Pending subject" };
initial.relations.links = [external, pending];
window.authorityCurrent = structuredClone(initial);
window.confirm = () => true;
createRoot(document.getElementById("root")!).render(<AuthorityRelations locale={locale} collection="works" id={initial.relations.source.internal_id!}
  initial={{ ok: true, envelope: initial }} canUpdate={!query.has("readonly")} canLookup={!query.has("denied")}
  search={async (type, filters) => {
    await new Promise(resolve => setTimeout(resolve, 100));
    if (window.authoritySearchStatus !== 200) return { ok: false, message: t.workEditorUnavailable };
    return { ok: true, page: { items: filters.uri === "urn:missing" ? [] : type === "Agent" ? [agent, { ...agent, internal_id: "00000000-0000-4000-8000-000000000004", uri: "urn:agent:duplicate-name" }] : [subject] } };
  }}
  reload={async () => ({ ok: true, envelope: { ...window.authorityCurrent, etag: '"fresh"' } })}
  mutate={async (_collection, _id, op, link, etag) => {
    window.authorityWrites.push({ op, link, etag });
    await new Promise(resolve => setTimeout(resolve, 50));
    if (window.authorityStatus !== 200) return { ok: false, status: window.authorityStatus, message: window.authorityStatus === 412 ? t.workEditorStale : t.authorityInvalid };
    const links = window.authorityCurrent.relations.links ?? [];
    window.authorityCurrent = { etag: '"saved"', labels: { [agent.uri]: authorityLabel(agent), [subject.uri]: authorityLabel(subject) }, relations: {
      ...initial.relations, links: op === "add" ? [...links, link] : links.filter(existing => !sameLink(existing, link)),
    } };
    return { ok: true, message: t.authoritySaved, envelope: window.authorityCurrent };
  }} />);
