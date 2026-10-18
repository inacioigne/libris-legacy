import { messages, type Locale } from "../../i18n/messages";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Only the public diagnostic fields cross the server action boundary.
export function workValidationFeedback(detail: unknown, locale: Locale): {
  message: string;
  issues: string[];
  requestId?: string;
  fieldErrors: Record<string, string>;
} {
  const problem = isRecord(detail) ? detail : {};
  const issues: string[] = [];
  const fieldErrors: Record<string, string> = {};
  const validation = isRecord(problem.validation) ? problem.validation : {};
  const diagnostics = [...(Array.isArray(problem.issues) ? problem.issues : []), ...(Array.isArray(validation.issues) ? validation.issues : [])];
  if (diagnostics.length) {
    for (const issue of diagnostics) {
      if (!isRecord(issue) || typeof issue.message !== "string") continue;
      const fields = Array.isArray(issue.field_ids)
        ? issue.field_ids.filter((field): field is string => typeof field === "string")
        : [];
      if (fields.length === 1 && /^\/(title|language|contribution|subject|note)(\/[a-z_0-9]+)*$/.test(fields[0])) fieldErrors[fields[0]] = issue.message;
      issues.push(fields.length ? `${fields.join(", ")}: ${issue.message}` : issue.message);
    }
  }
  const requestId = typeof problem.request_id === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(problem.request_id)
    ? problem.request_id : undefined;
  return {
    message: messages[locale].workFormValidationError,
    issues: [...new Set(issues)],
    fieldErrors,
    ...(requestId ? { requestId } : {}),
  };
}


export function workErrorFeedback(status: number, detail: unknown, locale: Locale) {
  const t = messages[locale];
  const feedback = workValidationFeedback(detail, locale);
  return {
    status,
    ...feedback,
    // Session/permission failures never expose identity or upstream diagnostics.
    ...(status === 401 || status === 403 ? { issues: [], fieldErrors: {} } : {}),
    message: status === 401 ? t.workEditorUnauthenticated
      : status === 403 ? t.workEditorForbidden
      : status === 409 ? t.workEditorConflict
      : status === 412 ? t.workEditorStale
      : status === 428 ? t.workEditorMissingVersion
      : status === 422 ? t.workFormValidationError
      : t.workEditorUnavailable,
  };
}
