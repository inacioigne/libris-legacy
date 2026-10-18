"use client";

import Link from "next/link";
import { type Dispatch, type SetStateAction, useActionState, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { messages, type Locale } from "@/i18n/messages";
import type { WorkRead } from "@/server/catalog";
import type { WorkFormState } from "@/server/catalog/actions";

type ActionFn = (
  prevState: WorkFormState | undefined,
  formData: FormData,
) => Promise<WorkFormState>;

type Props = {
  locale: Locale;
  mode: "create" | "edit";
  action: ActionFn;
  work?: WorkRead | null;
  workId?: string;
  ifMatch?: string;
  cancelHref: string;
};

function initialList<T>(items: T[] | null | undefined, fallback: T): T[] {
  return items && items.length > 0 ? items : [fallback];
}

function buildContributionList(work?: WorkRead | null): Array<{ agent: string; role: string }> {
  if (!work?.contribution || work.contribution.length === 0) return [{ agent: "", role: "" }];
  return work.contribution.map((entry) => ({
    agent: typeof entry.agent === "object" && entry.agent && "uri" in entry.agent && entry.agent.uri ? entry.agent.uri : "",
    role: entry.role?.[0] && typeof entry.role[0] === "object" && "uri" in entry.role[0] ? entry.role[0].uri ?? "" : "",
  }));
}

export function WorkEditorForm({ locale, mode, action, work, workId, ifMatch, cancelHref }: Props) {
  const t = messages[locale];
  const router = useRouter();
  const initialState: WorkFormState = { ok: true };
  const [state, formAction, isPending] = useActionState(action, initialState);
  const [title, setTitle] = useState(work?.title?.[0]?.main_title?.[0]?.value ?? "");
  const [subtitle, setSubtitle] = useState(work?.title?.[0]?.subtitle?.[0]?.value ?? "");
  const [languages, setLanguages] = useState<string[]>(
    initialList(work?.language?.map((entry) => entry.uri).filter(Boolean) ?? [], ""),
  );
  const [subjects, setSubjects] = useState<string[]>(
    initialList(work?.subject?.map((entry) => (typeof entry === "object" && entry && "uri" in entry ? entry.uri ?? "" : "")).filter(Boolean) ?? [], ""),
  );
  const [notes, setNotes] = useState<string[]>(
    initialList(work?.note?.map((entry) => entry.label?.[0]?.value ?? "").filter(Boolean) ?? [], ""),
  );
  const [contributors, setContributors] = useState<Array<{ agent: string; role: string }>>(
    buildContributionList(work),
  );

  useEffect(() => {
    if (state.ok && state.redirectTo) {
      router.push(state.redirectTo);
    }
  }, [router, state]);

  const updateList = (
    list: string[],
    index: number,
    value: string,
    setter: Dispatch<SetStateAction<string[]>>,
  ) => {
    const next = [...list];
    next[index] = value;
    setter(next);
  };

  return (
    <form action={formAction} className="mt-8 space-y-6 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
      <input type="hidden" name="locale" value={locale} />
      {mode === "edit" && workId ? <input type="hidden" name="id" value={workId} /> : null}
      {ifMatch ? <input type="hidden" name="ifMatch" value={ifMatch} /> : null}

      {state.message && !state.ok ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200" aria-live="polite">
          {state.message}
          {state.issues?.length ? (
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {state.issues.map((issue) => <li key={issue}>{issue}</li>)}
            </ul>
          ) : null}
          {state.requestId ? <p className="mt-2">{t.workFormRequestId}: {state.requestId}</p> : null}
        </div>
      ) : null}

      <div className="grid gap-6 md:grid-cols-2">
        <label className="flex flex-col gap-2 text-sm font-medium md:col-span-2">
          <span>{t.workFormTitleLabel}</span>
          <input
            name="title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
            placeholder="Ex: The Name of the Rose"
            required
          />
        </label>

        <label className="flex flex-col gap-2 text-sm font-medium md:col-span-2">
          <span>{t.workFormSubtitleLabel}</span>
          <input
            name="subtitle"
            value={subtitle}
            onChange={(event) => setSubtitle(event.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
            placeholder="Optional subtitle"
          />
        </label>
      </div>

      <p className="text-sm text-slate-600 dark:text-slate-300">{t.workFormUriHelp}</p>

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{t.workFormLanguageLabel}</h2>
          <button
            type="button"
            onClick={() => setLanguages((current) => [...current, ""])}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200"
          >
            {t.workFormAddItem}
          </button>
        </div>
        {languages.map((language, index) => (
          <div key={`language-${index}`} className="flex gap-2">
            <input
              name="language"
              value={language}
              onChange={(event) => updateList(languages, index, event.target.value, setLanguages)}
              className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
              placeholder="https://id.example.org/language/por"
            />
            <button
              type="button"
              onClick={() => setLanguages((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:text-red-200"
            >
              {t.workFormRemove}
            </button>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{t.workFormSubjectLabel}</h2>
          <button
            type="button"
            onClick={() => setSubjects((current) => [...current, ""])}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200"
          >
            {t.workFormAddItem}
          </button>
        </div>
        {subjects.map((subject, index) => (
          <div key={`subject-${index}`} className="flex gap-2">
            <input
              name="subject"
              value={subject}
              onChange={(event) => updateList(subjects, index, event.target.value, setSubjects)}
              className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
              placeholder="https://example.org/subject/fiction"
            />
            <button
              type="button"
              onClick={() => setSubjects((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:text-red-200"
            >
              {t.workFormRemove}
            </button>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{t.workFormContributorLabel}</h2>
          <button
            type="button"
            onClick={() => setContributors((current) => [...current, { agent: "", role: "" }])}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200"
          >
            {t.workFormAddItem}
          </button>
        </div>
        {contributors.map((contributor, index) => (
          <div key={`contribution-${index}`} className="grid gap-3 rounded-xl border border-slate-200 p-3 dark:border-gray-700 md:grid-cols-2">
            <label className="flex flex-col gap-2 text-sm font-medium">
              <span>{t.workFormContributorAgentLabel}</span>
              <input
                name="contribution_agent"
                value={contributor.agent}
                onChange={(event) => {
                  const next = [...contributors];
                  next[index] = { ...next[index], agent: event.target.value };
                  setContributors(next);
                }}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
                placeholder="https://example.org/agents/author"
              />
            </label>
            <label className="flex flex-col gap-2 text-sm font-medium">
              <span>{t.workFormContributorRoleLabel}</span>
              <input
                name="contribution_role"
                value={contributor.role}
                onChange={(event) => {
                  const next = [...contributors];
                  next[index] = { ...next[index], role: event.target.value };
                  setContributors(next);
                }}
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
                placeholder="https://id.loc.gov/vocabulary/relators/aut"
              />
            </label>
            <div className="md:col-span-2 flex justify-end">
              <button
                type="button"
                onClick={() => setContributors((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:text-red-200"
              >
                {t.workFormRemove}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{t.workFormNoteLabel}</h2>
          <button
            type="button"
            onClick={() => setNotes((current) => [...current, ""])}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200"
          >
            {t.workFormAddItem}
          </button>
        </div>
        {notes.map((note, index) => (
          <div key={`note-${index}`} className="flex gap-2">
            <input
              name="note"
              value={note}
              onChange={(event) => updateList(notes, index, event.target.value, setNotes)}
              className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-950"
              placeholder="A bibliographic note for the work"
            />
            <button
              type="button"
              onClick={() => setNotes((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:text-red-200"
            >
              {t.workFormRemove}
            </button>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3 pt-2">
        <Link href={cancelHref} className="rounded-lg border border-slate-300 px-4 py-2 font-medium text-slate-700 dark:border-gray-700 dark:text-slate-200">
          {t.workFormCancel}
        </Link>
        <button
          type="submit"
          disabled={isPending}
          className="rounded-lg bg-teal-700 px-4 py-2 font-medium text-white hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-70"
        >
          {isPending ? t.workFormLoading : t.workFormSave}
        </button>
      </div>
    </form>
  );
}
