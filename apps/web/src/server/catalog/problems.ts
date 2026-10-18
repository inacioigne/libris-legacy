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

export function instanceErrorFeedback(status: number, detail: unknown, locale: Locale) {
  const t = messages[locale];
  const feedback = workErrorFeedback(status, detail, locale);
  if (status === 401 || status === 403) return feedback;
  const problem = isRecord(detail) ? detail : {};
  const validation = isRecord(problem.validation) ? problem.validation : {};
  const diagnostics = [...(Array.isArray(problem.issues) ? problem.issues : []), ...(Array.isArray(validation.issues) ? validation.issues : [])];
  const issues: string[] = [];
  const fieldErrors: Record<string, string> = {};
  if (typeof problem.code === "string") issues.push(problem.code);
  for (const issue of diagnostics) {
    if (!isRecord(issue) || typeof issue.message !== "string") continue;
    const fields = Array.isArray(issue.field_ids) ? issue.field_ids.filter((field): field is string => typeof field === "string") : [];
    if (fields.length === 1 && /^\/(instance_of|title|edition_statement|provision_activity|identified_by|extent|carrier|note)(\/[a-z_0-9]+)*$/.test(fields[0])) fieldErrors[fields[0]] = issue.message;
    const context = [issue.code, ...fields, diagnosticPath(issue.path)].filter(value => typeof value === "string").join(" · ");
    issues.push(context ? `${context}: ${issue.message}` : issue.message);
  }
  return { ...feedback, issues: [...new Set(issues)], fieldErrors,
    message: status === 400 ? t.instanceBadRequest : status === 404 ? t.instanceNotFound : status === 422 ? t.instanceValidationError : feedback.message };
}

function diagnosticPath(path: unknown, depth = 0): string | undefined {
  if (typeof path === "string") return path;
  if (!isRecord(path) || depth > 8) return undefined;
  if (typeof path.iri === "string") return path.iri;
  const items = Array.isArray(path.items) ? path.items.map(item => diagnosticPath(item, depth + 1)).filter(Boolean).join(" / ") : "";
  return typeof path.kind === "string" ? `${path.kind}${items ? ` (${items})` : ""}` : undefined;
}
