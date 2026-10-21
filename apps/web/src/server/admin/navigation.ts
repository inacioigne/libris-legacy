import "server-only";
import type { components } from "../../generated/catalogographic-openapi";
import { messages, type Locale } from "../../i18n/messages";

// UI requirements only: grants and role mapping belong exclusively to the API.
export const administrativePermissions = [
  "bibliographic:create", "bibliographic:update", "bibliographic:delete",
  "authority:manage", "metadata_revision:read", "metadata_revision:create",
  "metadata_audit:read", "editorial:submit", "editorial:approve", "editorial:return",
  "editorial:publish", "editorial:withdraw", "editorial:republish", "editorial:archive",
  "system:admin",
] as const;
export type AdminPermission = components["schemas"]["Permission"];
export interface PermissionRequirement {
  mode: "all" | "any";
  requiredPermissions: readonly AdminPermission[];
}
export function satisfiesPermissions(
  effective: readonly string[], requirement: PermissionRequirement,
): boolean {
  if (requirement.requiredPermissions.length === 0) return false;
  const has = (permission: AdminPermission) => effective.includes(permission);
  return requirement.mode === "all"
    ? requirement.requiredPermissions.every(has)
    : requirement.requiredPermissions.some(has);
}
export const shellRequirement: PermissionRequirement = {
  mode: "any", requiredPermissions: administrativePermissions,
};

const descriptors = [
  { id: "cataloging", requiredPermissions: ["bibliographic:create", "bibliographic:update", "bibliographic:delete", "metadata_revision:create"] },
  { id: "authorities", requiredPermissions: ["authority:manage"] },
  { id: "history", requiredPermissions: ["metadata_revision:read", "metadata_audit:read"] },
  { id: "editorial", requiredPermissions: ["editorial:submit", "editorial:approve", "editorial:return", "editorial:publish", "editorial:withdraw", "editorial:republish", "editorial:archive"] },
  { id: "system", requiredPermissions: ["system:admin"] },
] as const satisfies readonly { id: string; requiredPermissions: readonly AdminPermission[] }[];
export interface AdminNavigationItem extends PermissionRequirement {
  id: (typeof descriptors)[number]["id"];
  label: string;
  href: string;
}
export function adminNavigation(effective: readonly string[], locale: Locale): AdminNavigationItem[] {
  return descriptors
    .filter((item) => satisfiesPermissions(effective, { ...item, mode: "any" }))
    .map((item) => ({ ...item, mode: "any", label: messages[locale][item.id], href: `/${locale}/admin#${item.id}` }));
}
