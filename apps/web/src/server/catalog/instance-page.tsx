import type { ReactNode } from "react";
import { messages, type Locale } from "../../i18n/messages";
import { guardAdminContent } from "../admin/access";
import { AdminAccessState, AdminFrame } from "../admin/shell";
import { requestIdentity } from "../auth/request";
import type { Identity } from "../auth/service";
import { CatalogAccessError } from "./index";
import { instanceErrorFeedback } from "./problems";

export async function instancePage(locale: Locale, operation: "read" | "create" | "update", render: (identity: Identity) => Promise<ReactNode>) {
  return guardAdminContent(requestIdentity, async access => {
    try { return <AdminFrame locale={locale}>{await render(access.identity)}</AdminFrame>; }
    catch (error) {
      const feedback = instanceErrorFeedback(error instanceof CatalogAccessError ? error.status : 503, error instanceof CatalogAccessError ? error.detail : undefined, locale);
      return <AdminFrame locale={locale}><h1 className="text-3xl font-semibold">{messages[locale].instances}</h1><div role="alert" className="my-5"><p>{feedback.message}</p>{feedback.requestId && <p>{messages[locale].workFormRequestId}: {feedback.requestId}</p>}{feedback.issues.map(issue => <p key={issue}>{issue}</p>)}</div><a className="underline" href={`/${locale}/admin/catalog/instances`}>{messages[locale].retry}</a></AdminFrame>;
    }
  }, access => <AdminFrame locale={locale}><AdminAccessState locale={locale} access={access} /></AdminFrame>, {
    mode: "all", requiredPermissions: operation === "read" ? ["metadata_revision:read"] : ["metadata_revision:read", operation === "create" ? "bibliographic:create" : "bibliographic:update"],
  });
}
