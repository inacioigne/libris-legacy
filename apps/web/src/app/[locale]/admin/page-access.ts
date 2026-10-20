import "server-only";

import { redirect } from "next/navigation";
import { PermissionRequirement } from "@/server/admin/navigation";
import { AdminAccess, guardAdminContent } from "@/server/admin/access";
import { requestIdentity } from "@/server/auth/request";
import { type Locale } from "@/i18n/messages";

export async function requireAdminPageAccess(
  locale: "pt" | "en",
  requirement: PermissionRequirement,
): Promise<Extract<AdminAccess, { status: "authorized" }>> {

  return guardAdminContent(
    requestIdentity,

    (access) => access,

    (access) => {
      switch (access.status) {
        case "login_required":
        case "session_expired":
          redirect(`/${locale}/admin/login`);

        case "access_denied":
          redirect(`/${locale}/admin/forbidden`);

        case "service_unavailable":
          redirect(`/${locale}/admin/unavailable`);
      }
    },

    requirement,
  );
}
