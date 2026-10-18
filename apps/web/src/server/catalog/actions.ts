"use server";

import {
  type WorkCreate,
  type WorkRead,
  type WorkUpdate,
  CatalogAccessError,
  createWork,
  updateWork,
} from "./index";
import { workValidationFeedback } from "./problems";

export type WorkFormState = {
  ok: boolean;
  message?: string;
  field?: string;
  redirectTo?: string;
  issues?: string[];
  requestId?: string;
};

function readString(value: FormDataEntryValue | null | undefined): string {
  return typeof value === "string" ? value.trim() : "";
}

function readList(formData: FormData, key: string): string[] {
  return formData
    .getAll(key)
    .map((value) => readString(value))
    .filter((value) => value.length > 0);
}

function addReference(value: string): { uri: string } | null {
  const uri = value.trim();
  return uri ? { uri } : null;
}

function buildTitleValue(
  title: string,
  subtitle?: string,
): WorkCreate["title"] {
  const mainTitle = title.trim();
  const subtitleValue = subtitle?.trim();
  const entry: WorkCreate["title"][number] = {
    kind: "Title",
    main_title: [{ value: mainTitle }],
  };
  if (subtitleValue) {
    entry.subtitle = [{ value: subtitleValue }];
  }
  return [entry];
}

function buildNoteValue(note: string): { label: { value: string }[] } | null {
  const value = note.trim();
  return value ? { label: [{ value }] } : null;
}

function buildContributionEntries(
  agentValues: string[],
  roleValues: string[],
): Array<{ agent: { uri: string }; role: { uri: string }[] }> {
  return Array.from(
    { length: Math.max(agentValues.length, roleValues.length) },
    (_, index) => {
      const agent = addReference(agentValues[index] ?? "");
      const role = addReference(roleValues[index] ?? "");
      if (!agent || !role) {
        return null;
      }

      return {
        agent,
        role: [role],
      };
    },
  ).filter(
    (value): value is { agent: { uri: string }; role: { uri: string }[] } =>
      Boolean(value),
  );
}

function buildCreatePayload(formData: FormData): WorkCreate {
  const title = readString(formData.get("title"));
  const subtitle = readString(formData.get("subtitle"));
  const language = readList(formData, "language").map((uri) => ({ uri }));
  const subject = readList(formData, "subject").map((uri) => ({ uri }));
  const agentValues = readList(formData, "contribution_agent");
  const roleValues = readList(formData, "contribution_role");
  const notes = readList(formData, "note")
    .map((value) => ({ label: [{ value }] }))
    .filter(Boolean);

  const contribution = buildContributionEntries(agentValues, roleValues);

  const payload: WorkCreate = {
    title: buildTitleValue(title, subtitle),
  };

  if (language.length > 0) {
    payload.language = language;
  }
  if (subject.length > 0) {
    payload.subject = subject;
  }
  if (contribution.length > 0) {
    payload.contribution = contribution;
  }
  if (notes.length > 0) {
    payload.note = notes;
  }

  return payload;
}

function buildUpdatePayload(formData: FormData): WorkUpdate {
  const title = readString(formData.get("title"));
  const subtitle = readString(formData.get("subtitle"));
  const language = readList(formData, "language").map((uri) => ({ uri }));
  const subject = readList(formData, "subject").map((uri) => ({ uri }));
  const agentValues = readList(formData, "contribution_agent");
  const roleValues = readList(formData, "contribution_role");
  const notes = readList(formData, "note")
    .map((value) => ({ label: [{ value }] }))
    .filter(Boolean);

  const payload: WorkUpdate = {};

  if (title) {
    payload.title = buildTitleValue(title, subtitle);
  }
  if (language.length > 0) {
    payload.language = language;
  }
  if (subject.length > 0) {
    payload.subject = subject;
  }
  if (agentValues.length > 0 || roleValues.length > 0) {
    const contribution = buildContributionEntries(agentValues, roleValues);
    if (contribution.length > 0) {
      payload.contribution = contribution;
    }
  }
  if (notes.length > 0) {
    payload.note = notes;
  }

  return payload;
}

export async function createWorkAction(
  _prevState: WorkFormState | undefined,
  formData: FormData,
): Promise<WorkFormState> {
  const locale = readString(formData.get("locale")) || "pt";
  const payload = buildCreatePayload(formData);
  if (!payload.title?.[0]?.main_title?.[0]?.value?.trim()) {
    return {
      ok: false,
      message: "The main title is required.",
      field: "title",
    };
  }
 

  try {
    const created = await createWork(payload);
     console.log("[ACTION..]", created);
    return {
      ok: true,
      message: "Work saved successfully.",
      redirectTo: `/${locale}/admin/catalog/works/${created.internal_id}`,
    };
  } catch (error) {
    if (error instanceof CatalogAccessError && error.status === 422) {
      return {
        ok: false,
        field: "submit",
        ...workValidationFeedback(error.detail, locale === "en" ? "en" : "pt"),
      };
    }
    return {
      ok: false,
      message:
        error instanceof Error ? error.message : "Could not create the work.",
      field: "submit",
    };
  }
}

export async function updateWorkAction(
  _prevState: WorkFormState | undefined,
  formData: FormData,
): Promise<WorkFormState> {
  const locale = readString(formData.get("locale")) || "pt";
  const id = readString(formData.get("id"));
  const ifMatch = readString(formData.get("ifMatch"));
  if (!id) {
    return {
      ok: false,
      message: "The work identifier is missing.",
      field: "id",
    };
  }
  if (!ifMatch) {
    return {
      ok: false,
      message: "ETag is missing. Reload the work before editing it again.",
      field: "submit",
    };
  }

  const payload = buildUpdatePayload(formData);
  if (!payload.title?.[0]?.main_title?.[0]?.value?.trim()) {
    return {
      ok: false,
      message: "The main title is required.",
      field: "title",
    };
  }

  try {
    const updated = await updateWork(id, payload, { ifMatch });
    return {
      ok: true,
      message: "Work saved successfully.",
      redirectTo: `/${locale}/admin/catalog/works/${updated.internal_id}`,
    };
  } catch (error) {
    if (error instanceof CatalogAccessError && error.status === 422) {
      return {
        ok: false,
        field: "submit",
        ...workValidationFeedback(error.detail, locale === "en" ? "en" : "pt"),
      };
    }
    return {
      ok: false,
      message:
        error instanceof Error ? error.message : "Could not update the work.",
      field: "submit",
    };
  }
}
