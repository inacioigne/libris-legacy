import { messages, type Locale } from "../../i18n/messages";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Only the public diagnostic fields cross the server action boundary.
export function workValidationFeedback(detail: unknown, locale: Locale): {
  message: string;
  issues: string[];
  requestId?: string;
} {
  const problem = isRecord(detail) ? detail : {};
  const issues: string[] = [];
  if (Array.isArray(problem.issues)) {
    for (const issue of problem.issues) {
      if (!isRecord(issue) || typeof issue.message !== "string") continue;
      const fields = Array.isArray(issue.field_ids)
        ? issue.field_ids.filter((field): field is string => typeof field === "string")
        : [];
      issues.push(fields.length ? `${fields.join(", ")}: ${issue.message}` : issue.message);
    }
  }
  const requestId = typeof problem.request_id === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(problem.request_id)
    ? problem.request_id : undefined;
  return {
    message: messages[locale].workFormValidationError,
    issues: [...new Set(issues)],
    ...(requestId ? { requestId } : {}),
  };
}
