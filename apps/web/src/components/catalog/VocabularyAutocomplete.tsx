"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  applySelectedConcept,
  conceptPresentation,
  conceptSearchParams,
  keepResolution,
  VocabularyQueryGate,
  type SelectedConceptState,
  type VocabularyConcept,
  type VocabularyReference,
  type VocabularySearchQuery,
} from "../../catalog/vocabularies";
import { messages, type Locale } from "../../i18n/messages";
import type { VocabularyResolveResult, VocabularySearchResult } from "../../catalog/vocabularies";

const buttonClass = "rounded-lg border border-slate-400 px-3 py-2 text-sm font-medium disabled:opacity-50";

const inputClass = "w-full rounded-lg border border-slate-400 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-950";

export type VocabularySearchFn = (query: VocabularySearchQuery) => Promise<VocabularySearchResult>;
export type VocabularyResolveFn = (vocabularyUri: string, uri: string, locale: Locale) => Promise<VocabularyResolveResult>;

export function VocabularyAutocomplete<T extends VocabularyReference>({
  locale, vocabularyUri, values, onChange, path, errors, search, resolve, label, debounceMs = 300, disabled,
}: {
  locale: Locale;
  vocabularyUri: string;
  values: T[];
  onChange: (values: T[]) => void;
  path: string;
  errors?: Record<string, string>;
  search: VocabularySearchFn;
  resolve: VocabularyResolveFn;
  label: string;
  debounceMs?: number;
  disabled?: boolean;
}) {
  const t = messages[locale];
  const baseId = useId();
  const listId = `${baseId}-list`;
  const statusId = `${baseId}-status`;
  const inputRef = useRef<HTMLInputElement>(null);
  const gate = useRef(new VocabularyQueryGate());
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [page, setPage] = useState<VocabularySearchResult["page"]>();
  const [active, setActive] = useState(0);
  const [resolutions, setResolutions] = useState<SelectedConceptState[]>([]);
  const [retry, setRetry] = useState(0);
  const items = page?.items ?? [];

  useEffect(() => {
    let cancelled = false;
    void Promise.all(values.map(async (entry, index) => {
      if (!entry.uri) return { index, state: { kind: "unrecognized" } as SelectedConceptState };
      try {
        const result = await resolve(vocabularyUri, entry.uri, locale);
        return { index, state: result.ok ? result.state : result.state.kind === "unrecognized" ? result.state : { kind: "unavailable" as const } };
      } catch {
        return { index, state: { kind: "unavailable" as const } };
      }
    })).then(results => {
      if (cancelled) return;
      setResolutions(current => {
        const next = values.map((_, index) => current[index]);
        for (const result of results) next[result.index] = keepResolution(current[result.index], result.state);
        return next;
      });
    });
    return () => { cancelled = true; };
  }, [values, vocabularyUri, locale, resolve]);

  useEffect(() => {
    const params = conceptSearchParams(query);
    if (!params) {
      gate.current.next();
      setLoading(false); setError(undefined); setPage(undefined); setActive(0);
      return;
    }
    const generation = gate.current.next();
    setLoading(true); setError(undefined); setOpen(true);
    const timer = window.setTimeout(async () => {
      try {
        const result = await search({ vocabularyUri, locale, limit: 20, offset: 0, ...params });
        if (!gate.current.isCurrent(generation)) return;
        if (!result.ok) { setPage(undefined); setError(result.status === 403 ? t.vocabularyPermission : t.vocabularySearchError); }
        else { setPage(result.page); setError(undefined); setActive(0); }
      } catch {
        if (!gate.current.isCurrent(generation)) return;
        setPage(undefined); setError(t.vocabularySearchError);
      } finally {
        if (gate.current.isCurrent(generation)) setLoading(false);
      }
    }, debounceMs);
    return () => window.clearTimeout(timer);
  }, [query, vocabularyUri, locale, search, debounceMs, retry, t.vocabularyPermission, t.vocabularySearchError]);

  function select(concept: VocabularyConcept) {
    onChange([...values, applySelectedConcept<T>(undefined, concept.uri)]);
    setQuery("");
    setPage(undefined);
    setOpen(false);
    inputRef.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (!items.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(index => (index + 1) % items.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(index => (index - 1 + items.length) % items.length);
    } else if (event.key === "Enter" && open) {
      event.preventDefault();
      const concept = items[active];
      if (concept) select(concept);
    }
  }

  const statusText = loading ? t.vocabularyLoading
    : error ? t.vocabularySearchError
    : query && page && items.length === 0 ? t.vocabularyEmpty
    : undefined;

  return <fieldset disabled={disabled} className="min-w-0 space-y-3 rounded-xl border border-slate-300 p-4 dark:border-gray-700">
    <legend className="px-2 font-semibold">{label}</legend>
    <ul className="space-y-3">
      {values.map((entry, index) => {
        const state = resolutions[index];
        const errorMessage = errors?.[`${path}/${index}/uri`];
        const inputId = `${baseId}-uri-${index}`;
        const helpId = `${inputId}-help`;
        const errorId = `${inputId}-error`;
        const presentation = state && "concept" in state && state.concept
          ? conceptPresentation(state.concept)
          : entry.uri;
        return <li key={`${index}-${entry.uri}`} className="space-y-2 rounded-lg border border-slate-200 p-3 dark:border-gray-700">
          <p>{t.vocabularySelected}: <span className="font-medium">{presentation}</span></p>
          {state?.kind === "deprecated" && <p role="status">{t.vocabularyDeprecated}</p>}
          {state?.kind === "unverified" && <p role="status">{t.vocabularyUnverified}</p>}
          {state?.kind === "unrecognized" && <p role="status">{t.vocabularyUnrecognized}</p>}
          {state?.kind === "unavailable" && <p role="status">{t.vocabularyUnavailable}</p>}
          <label htmlFor={inputId} className="block text-sm font-medium">{t.vocabularyConceptUri}</label>
          <input id={inputId} name={`${path}/${index}/uri`} value={entry.uri} readOnly className={inputClass}
            aria-invalid={Boolean(errorMessage)} aria-describedby={`${helpId}${errorMessage ? ` ${errorId}` : ""}`} />
          <p id={helpId} className="break-all text-xs text-slate-600 dark:text-slate-300">{t.vocabularyInspect}</p>
          {errorMessage && <p id={errorId} className="text-sm text-red-700 dark:text-red-300">{errorMessage}</p>}
          <button type="button" className={buttonClass} aria-label={`${t.vocabularyRemove}: ${presentation}`}
            onClick={() => onChange(values.filter((_, current) => current !== index))}>{t.vocabularyRemove}</button>
        </li>;
      })}
    </ul>
    <div className="space-y-1">
      <label htmlFor={`${baseId}-search`} className="block text-sm font-medium">{t.vocabularySearch}</label>
      <input ref={inputRef} id={`${baseId}-search`} role="combobox" autoComplete="off" value={query}
        onChange={event => { setQuery(event.target.value); setOpen(true); }}
        onKeyDown={onKeyDown} onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        aria-autocomplete="list" aria-expanded={open} aria-controls={listId}
        aria-activedescendant={open && items[active] ? `${listId}-option-${active}` : undefined}
        aria-describedby={statusId} className={inputClass} />
      <div id={statusId} role="status" aria-live="polite" className="text-sm">
        {statusText}
        {error && <button type="button" className={`${buttonClass} ml-2`} onClick={() => setRetry(count => count + 1)}>{t.vocabularyRetry}</button>}
      </div>
      {open && items.length > 0 && <ul id={listId} role="listbox" aria-multiselectable="true" className="max-h-64 overflow-auto rounded-lg border border-slate-400 bg-white dark:border-gray-600 dark:bg-gray-950">
        {items.map((concept, index) => <li key={concept.concept_id} id={`${listId}-option-${index}`} role="option"
          aria-selected={index === active} className={`cursor-pointer break-all px-3 py-2 ${index === active ? "bg-teal-100 dark:bg-teal-900" : ""}`}
          onMouseDown={event => event.preventDefault()}
          onClick={() => select(concept)}>
          <span className="font-medium">{conceptPresentation(concept)}</span>
          <span className="block text-xs">{concept.uri}</span>
          {concept.status === "deprecated" && <span className="block text-xs">{t.vocabularyDeprecated}</span>}
          {concept.status === "unverified" && <span className="block text-xs">{t.vocabularyUnverified}</span>}
        </li>)}
      </ul>}
    </div>
  </fieldset>;
}
