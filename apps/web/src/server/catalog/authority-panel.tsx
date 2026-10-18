import { ConnectedAuthorityRelations } from "../../components/catalog/ConnectedAuthorityRelations";
import type { RelationCollection } from "../../catalog/authorities";
import type { Locale } from "../../i18n/messages";
import { changeAuthorityRelationAction, reloadAuthorityRelationsAction, searchAuthoritiesAction } from "./authority-actions";

export async function AuthorityPanel({ collection, id, locale, permissions }: { collection: RelationCollection; id: string; locale: Locale; permissions: string[] }) {
  const initial = await reloadAuthorityRelationsAction(collection, id, locale);
  return <ConnectedAuthorityRelations collection={collection} id={id} locale={locale} initial={initial}
    canUpdate={permissions.includes("bibliographic:update")} canLookup={permissions.includes("authority:read") && permissions.includes("metadata_revision:read")}
    search={searchAuthoritiesAction} reload={reloadAuthorityRelationsAction} mutate={changeAuthorityRelationAction} />;
}
