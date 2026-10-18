"use client";

import { useId, useRef, type ReactNode } from "react";
import { messages, type Locale } from "../../i18n/messages";
import type { LocalizedText, WorkDraft } from "../../catalog/work-editor";

const inputClass = "w-full rounded-lg border border-slate-400 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-950";
export const buttonClass = "rounded-lg border border-slate-400 px-3 py-2 text-sm font-medium disabled:opacity-50";
type Context = { locale: Locale; errors?: Record<string, string> };

export function EditorInput({ label, value, onChange, path, required, context }: {
  label: string; value: string; onChange: (value: string) => void; path: string; required?: boolean; context: Context;
}) {
  const id = useId();
  const error = context.errors?.[path];
  return <div className="min-w-0 space-y-1">
    <label htmlFor={id} className="block text-sm font-medium">{label}{required ? " *" : ""}</label>
    <input id={id} name={path} value={value} onChange={event => onChange(event.target.value)} required={required}
      className={inputClass} aria-invalid={Boolean(error)} aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`} />
    <span id={`${id}-help`} className="text-xs text-slate-600 dark:text-slate-300">{required ? messages[context.locale].workEditorRequired : messages[context.locale].workEditorOptional}</span>
    {error && <p id={`${id}-error`} className="text-sm text-red-700 dark:text-red-300">{error}</p>}
  </div>;
}

// A presentation primitive for repeated rows, not a profile/form engine.
export function Rows<T>({ label, values, onChange, empty, children, locale }: {
  label: string; values: T[]; onChange: (values: T[]) => void; empty: () => T;
  children: (value: T, index: number, change: (value: T) => void) => ReactNode; locale: Locale;
}) {
  const group = useRef<HTMLFieldSetElement>(null);
  const keys = useRef<number[]>([]);
  const nextKey = useRef(0);
  while (keys.current.length < values.length) keys.current.push(nextKey.current++);
  keys.current.length = values.length;
  const t = messages[locale];
  function focusRow(index: number) {
    requestAnimationFrame(() => {
      const rows = group.current?.querySelectorAll<HTMLElement>(":scope > [data-editor-row]");
      const row = rows?.[Math.min(index, rows.length - 1)];
      (row?.querySelector<HTMLElement>("input, select, button") ?? group.current?.querySelector<HTMLButtonElement>(":scope > button"))?.focus();
    });
  }
  return <fieldset ref={group} className="min-w-0 space-y-3 rounded-xl border border-slate-300 p-4 dark:border-gray-700">
    <legend className="px-2 font-semibold">{label}</legend>
    {values.map((value, index) => <div data-editor-row key={keys.current[index]} className="space-y-3 border-b border-slate-200 pb-3 dark:border-gray-700">
      {children(value, index, item => onChange(values.map((current, i) => i === index ? item : current)))}
      <button type="button" className={buttonClass} aria-label={`${t.workFormRemove}: ${label} ${index + 1}`} onClick={() => {
        keys.current.splice(index, 1);
        onChange(values.filter((_, i) => i !== index)); focusRow(index);
      }}>{t.workFormRemove} — {label} {index + 1}</button>
    </div>)}
    <button type="button" className={buttonClass} onClick={() => { onChange([...values, empty()]); focusRow(values.length); }}>{t.workFormAddItem} — {label}</button>
  </fieldset>;
}

function Texts({ label, values, onChange, path, context }: {
  label: string; values: LocalizedText[]; onChange: (values: LocalizedText[]) => void; path: string; context: Context;
}) {
  return <Rows<LocalizedText> label={label} values={values} onChange={onChange} empty={() => ({ value: "" })} locale={context.locale}>
    {(text, index, change) => <div className="grid gap-3 md:grid-cols-2">
      <EditorInput label={label} path={`${path}/${index}/value`} value={text.value} onChange={value => change({ ...text, value })} required context={context} />
      <EditorInput label={messages[context.locale].workEditorTextLanguage} path={`${path}/${index}/language`} value={text.language ?? ""}
        onChange={language => change({ ...text, language: language || null })} context={context} />
    </div>}
  </Rows>;
}

type Reference = WorkDraft["subject"][number];
function ReferenceInput({ reference, onChange, label, path, context }: {
  reference: Reference; onChange: (reference: Reference) => void; label: string; path: string; context: Context;
}) {
  // A structured authority reference is never demoted to a URI-only reference.
  if ("kind" in reference) return <div><p>{messages[context.locale].workEditorStructuredReference}</p><pre className="whitespace-pre-wrap break-all text-sm">{JSON.stringify(reference, null, 2)}</pre></div>;
  return <EditorInput label={label} value={reference.uri} onChange={uri => onChange({ ...reference, uri })} path={`${path}/uri`} required context={context} />;
}

export function WorkEditorFields({ draft, onChange, locale, errors }: {
  draft: WorkDraft; onChange: (draft: WorkDraft) => void; locale: Locale; errors?: Record<string, string>;
}) {
  const t = messages[locale];
  const context = { locale, errors };
  return <>
    <Rows<WorkDraft["title"][number]> label={t.workEditorTitles} values={draft.title} onChange={title => onChange({ ...draft, title })}
      empty={() => ({ kind: "VariantTitle", main_title: [{ value: "" }] })} locale={locale}>
      {(title, index, change) => <>
        <label className="block text-sm font-medium">{t.workEditorTitleKind}
          <select aria-label={`${t.workEditorTitleKind} ${index + 1}`} className={inputClass} value={title.kind ?? "Title"} onChange={event => change({ ...title, kind: event.target.value === "Title" ? "Title" : "VariantTitle" })}>
            <option value="Title">{t.workFormTitleLabel}</option><option value="VariantTitle">{t.workEditorVariant}</option>
          </select>
        </label>
        <Texts label={t.workFormTitleLabel} values={title.main_title} onChange={main_title => change({ ...title, main_title })} path={`/title/${index}/main_title`} context={context} />
        <Texts label={t.workFormSubtitleLabel} values={title.subtitle ?? []} onChange={subtitle => change({ ...title, subtitle })} path={`/title/${index}/subtitle`} context={context} />
      </>}
    </Rows>
    <p id="work-uri-help" className="text-sm">{t.workFormUriHelp}</p>
    <Rows label={t.workFormLanguageLabel} values={draft.language} onChange={language => onChange({ ...draft, language })} empty={() => ({ uri: "" })} locale={locale}>
      {(entry, index, change) => <EditorInput label={`${t.workFormLanguageLabel} ${index + 1} (URI)`} value={entry.uri} onChange={uri => change({ ...entry, uri })} path={`/language/${index}/uri`} required context={context} />}
    </Rows>
    <Rows<WorkDraft["contribution"][number]> label={t.workFormContributorLabel} values={draft.contribution} onChange={contribution => onChange({ ...draft, contribution })} empty={() => ({ agent: { uri: "" }, role: [{ uri: "" }] })} locale={locale}>
      {(entry, index, change) => <>
        <ReferenceInput label={t.workFormContributorAgentLabel} reference={entry.agent} onChange={agent => change({ ...entry, agent })} path={`/contribution/${index}/agent`} context={context} />
        <Rows label={t.workFormContributorRoleLabel} values={entry.role} onChange={role => change({ ...entry, role })} empty={() => ({ uri: "" })} locale={locale}>
          {(role, roleIndex, changeRole) => <EditorInput label={`${t.workFormContributorRoleLabel} ${roleIndex + 1} (URI)`} value={role.uri} onChange={uri => changeRole({ ...role, uri })} path={`/contribution/${index}/role/${roleIndex}/uri`} required context={context} />}
        </Rows>
      </>}
    </Rows>
    <Rows<WorkDraft["subject"][number]> label={t.workFormSubjectLabel} values={draft.subject} onChange={subject => onChange({ ...draft, subject })} empty={() => ({ uri: "" })} locale={locale}>
      {(entry, index, change) => <ReferenceInput label={`${t.workFormSubjectLabel} ${index + 1}`} reference={entry} onChange={change} path={`/subject/${index}`} context={context} />}
    </Rows>
    <Rows<WorkDraft["note"][number]> label={t.workFormNoteLabel} values={draft.note} onChange={note => onChange({ ...draft, note })} empty={() => ({ label: [{ value: "" }] })} locale={locale}>
      {(note, index, change) => <>
        <Texts label={t.workFormNoteLabel} values={note.label} onChange={label => change({ ...note, label })} path={`/note/${index}/label`} context={context} />
        <Rows label={t.workEditorNoteType} values={note.note_type ?? []} onChange={note_type => change({ ...note, note_type })} empty={() => ""} locale={locale}>
          {(value, typeIndex, changeType) => <EditorInput label={t.workEditorNoteType} value={value} onChange={changeType} path={`/note/${index}/note_type/${typeIndex}`} required context={context} />}
        </Rows>
      </>}
    </Rows>
  </>;
}
