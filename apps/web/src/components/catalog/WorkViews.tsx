import type { WorkRead } from "../../catalog/work-editor";
import { messages, type Locale } from "../../i18n/messages";

type Reference = NonNullable<WorkRead["subject"]>[number];

function localized(value: { value: string; language?: string | null }): string {
  return `${value.value}${value.language ? ` (${value.language})` : ""}`;
}

export function workTitle(work: WorkRead, locale: Locale): string {
  return (
    work.title
      .filter((title) => title.kind === "Title")
      .flatMap((title) => title.main_title)
      .map((text) => text.value)
      .filter(Boolean)
      .join(" / ") || messages[locale].workUntitled
  );
}

export function referenceIdentity(reference: Reference): string {
  if ("kind" in reference) {
    if (reference.kind === "local")
      return [reference.uri, reference.internal_id].filter(Boolean).join(" — ");
    if (reference.kind === "external")
      return [reference.uri, reference.external_id].filter(Boolean).join(" — ");
    return `${reference.label} — ${reference.namespace}${reference.key}`;
  }
  return reference.uri;
}

export function workPresentation(work: WorkRead) {
  return {
    preferredTitles: work.title
      .filter((title) => title.kind === "Title")
      .map((title) => [...title.main_title, ...(title.subtitle ?? [])].map(localized).join(" — ")),
    variantTitles: work.title
      .filter((title) => title.kind === "VariantTitle")
      .map((title) => [...title.main_title, ...(title.subtitle ?? [])].map(localized).join(" — ")),
    languages: work.language?.map((entry) => entry.uri),
    contentTypes: work.content?.map((entry) => entry.uri),
    subjects: work.subject?.map(referenceIdentity),
    relatedWorks: work.related_to?.map(referenceIdentity),
    translations: work.translation_of?.map(referenceIdentity),
    classifications: work.classification?.map((entry) => {
      const sources = (entry.source ?? []).map((source) => source.uri);
      return [...entry.classification_portion, ...sources].join(" — ");
    }),
    contributions: work.contribution?.map((entry) => ({
      agent: referenceIdentity(entry.agent),
      roles: entry.role.map((role) => role.uri),
    })),
    notes: work.note?.map((note) => ({
      labels: note.label.map(localized),
      types: note.note_type ?? [],
    })),
  };
}

function Values({ values }: { values?: readonly string[] }) {
  return values?.length ? (
    <ul className="space-y-1">
      {values.map((value, index) => (
        <li key={`${value}-${index}`} className="break-all">
          {value}
        </li>
      ))}
    </ul>
  ) : (
    <span>—</span>
  );
}

function Field({ label, values }: { label: string; values?: readonly string[] }) {
  return (
    <div className="space-y-1">
      <dt className="text-sm font-medium text-slate-600 dark:text-slate-300">{label}</dt>
      <dd><Values values={values} /></dd>
    </div>
  );
}

export function WorkDescription({ work, locale }: { work: WorkRead; locale: Locale }) {
  const t = messages[locale];
  const presentation = workPresentation(work);

  return (
    <div className="space-y-6 break-words">
      <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <h2 className="text-xl font-semibold">{t.workBibliographic}</h2>
        <dl className="grid gap-5 md:grid-cols-2">
          <Field label={t.workPreferredTitles} values={presentation.preferredTitles} />
          <Field label={t.workVariantTitles} values={presentation.variantTitles} />
          <Field label={t.workFormLanguageLabel} values={presentation.languages} />
          <Field label={t.workContentTypes} values={presentation.contentTypes} />
          <Field label={t.workSubjects} values={presentation.subjects} />
          <Field label={t.workRelatedWorks} values={presentation.relatedWorks} />
          <Field label={t.workTranslations} values={presentation.translations} />
          <Field label={t.workClassifications} values={presentation.classifications} />
        </dl>

        <section className="space-y-3 border-t border-slate-200 pt-4 dark:border-gray-700">
          <h3 className="font-semibold">{t.workFormContributorLabel}</h3>
          {presentation.contributions?.length ? (
            <ul className="space-y-3">
              {presentation.contributions.map((contribution, index) => (
                <li key={index} className="rounded-lg border border-slate-200 p-3 dark:border-gray-700">
                  <p className="break-all">{contribution.agent}</p>
                  <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
                    {t.workFormContributorRoleLabel}: {contribution.roles.join(" / ")}
                  </p>
                </li>
              ))}
            </ul>
          ) : <p>—</p>}
        </section>

        <section className="space-y-3 border-t border-slate-200 pt-4 dark:border-gray-700">
          <h3 className="font-semibold">{t.workFormNoteLabel}</h3>
          {presentation.notes?.length ? (
            <ul className="space-y-2">
              {presentation.notes.map((note, index) => (
                <li key={index}>
                  {note.labels.join(" / ")}
                  {note.types.length ? ` — ${note.types.join(" / ")}` : ""}
                </li>
              ))}
            </ul>
          ) : <p>—</p>}
        </section>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <h2 className="text-xl font-semibold">{t.instanceIdentity}</h2>
        <dl className="mt-4 grid gap-4 md:grid-cols-2">
          <Field label={t.workIdentifier} values={[work.internal_id]} />
          <Field label={t.workUri} values={[work.uri]} />
        </dl>
      </section>

      <details className="rounded-2xl border border-slate-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <summary className="cursor-pointer font-semibold">{t.instanceReadOnly}</summary>
        <p className="mt-3 text-sm">{t.workProjectionHelp}</p>
        <pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-sm">{JSON.stringify(work, null, 2)}</pre>
      </details>
    </div>
  );
}
