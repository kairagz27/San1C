import { useState, type ReactNode } from "react";
import {
  AlertCircle,
  CalendarDays,
  Check,
  ChevronDown,
  Database,
  Info,
  LoaderCircle,
  MapPin,
  Shapes,
  SlidersHorizontal,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type {
  DateRange,
  Filters,
  Meta,
  Named,
  Period,
  Selection,
} from "./types";
import { HttpError, momentText, rangeText } from "./lib";
import { ru } from "./ru";

export function Feedback({
  error,
  retry,
}: {
  error?: Error;
  retry?: () => void;
}) {
  return (
    <div className="feedback" role={error ? "alert" : "status"}>
      {error ? <AlertCircle /> : <LoaderCircle className="spin" />}
      {error
        ? error instanceof HttpError && error.status === 422
          ? ru.invalidRequest
          : ru.networkError
        : ru.loading}
      {error && retry && (
        <button className="button" onClick={retry}>
          {ru.retry}
        </button>
      )}
    </div>
  );
}
export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="notice" role="status">
      <Info size={17} />
      <div>{children}</div>
    </div>
  );
}
export function coverageLabel(meta: Meta) {
  if (meta.complete && meta.previous_complete !== false) return ru.complete;
  return meta.current_complete && meta.previous_complete === false
    ? ru.incompleteComparison
    : ru.incomplete;
}
export function SelectControl({
  icon: Icon,
  label,
  value,
  options,
  onChange,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  options: Named[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="select-wrap">
      <Icon size={16} />
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option value={o.id} key={o.id}>
            {o.name}
          </option>
        ))}
      </select>
      <ChevronDown size={13} />
    </label>
  );
}
export function FilterBar({
  selection,
  options,
  range,
  onChange,
  onlyDimensions = false,
}: {
  selection: Selection;
  options: { points: Named[]; categories: Named[] };
  range?: DateRange;
  onChange: (s: Selection) => void;
  onlyDimensions?: boolean;
}) {
  const [custom, setCustom] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [invalid, setInvalid] = useState(false);
  function choosePeriod(v: string) {
    if (v === "custom") {
      setStart(selection.start ?? range?.start ?? "");
      setEnd(selection.end ?? range?.end ?? "");
      setInvalid(false);
      setCustom(true);
    } else {
      setCustom(false);
      onChange({
        ...selection,
        period: v as Period,
        start: undefined,
        end: undefined,
      });
    }
  }
  return (
    <div className="filter-block">
      <div className="filter-bar">
        {!onlyDimensions && (
          <SelectControl
            icon={CalendarDays}
            label={ru.period}
            value={custom ? "custom" : selection.period}
            options={Object.entries(ru.periods).map(([id, name]) => ({
              id,
              name,
            }))}
            onChange={choosePeriod}
          />
        )}
        <SelectControl
          icon={MapPin}
          label={ru.point}
          value={selection.point ?? ""}
          options={[{ id: "", name: ru.allPoints }, ...options.points]}
          onChange={(point) => onChange({ ...selection, point: point || null })}
        />
        <SelectControl
          icon={Shapes}
          label={ru.category}
          value={selection.category ?? ""}
          options={[{ id: "", name: ru.allCategories }, ...options.categories]}
          onChange={(category) =>
            onChange({ ...selection, category: category || null })
          }
        />
        {(selection.point ||
          selection.category ||
          (!onlyDimensions && selection.period !== "last_30_days")) && (
          <button
            className="text-btn"
            onClick={() => {
              setCustom(false);
              onChange({ period: "last_30_days" });
            }}
          >
            <X size={14} />
            {ru.reset}
          </button>
        )}
        {!onlyDimensions && selection.period === "custom" && !custom && (
          <button className="text-btn" onClick={() => choosePeriod("custom")}>
            <SlidersHorizontal size={15} />
            {ru.period}
          </button>
        )}
      </div>
      {!onlyDimensions && custom && (
        <form
          className="date-editor"
          onSubmit={(e) => {
            e.preventDefault();
            if (!start || !end || start > end) {
              setInvalid(true);
              return;
            }
            onChange({ ...selection, period: "custom", start, end });
            setCustom(false);
          }}
        >
          <label>
            {ru.start}
            <input
              type="date"
              value={start}
              required
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            {ru.end}
            <input
              type="date"
              value={end}
              required
              min={start}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <button className="button">{ru.apply}</button>
          <button
            type="button"
            className="icon-btn"
            aria-label={ru.close}
            onClick={() => setCustom(false)}
          >
            <X size={17} />
          </button>
          {invalid && <span role="alert">{ru.invalidDates}</span>}
        </form>
      )}
    </div>
  );
}
export function Details({
  meta,
  range,
  previous,
  filters,
  options,
}: {
  meta: Meta;
  range: DateRange;
  previous?: DateRange | null;
  filters: Filters;
  options?: { points: Named[]; categories: Named[] };
}) {
  const description = [
    options?.points.find((v) => v.id === filters.point)?.name ??
      filters.point ??
      ru.allPoints,
    options?.categories.find((v) => v.id === filters.category)?.name ??
      filters.category ??
      ru.allCategories,
  ].join(" · ");
  return (
    <details className="report-details">
      <summary>
        <Database size={15} />
        {ru.reportInfo}
        <ChevronDown size={14} />
      </summary>
      <dl className="facts">
        <dt>{ru.period}</dt>
        <dd>{rangeText(range)}</dd>
        {previous && (
          <>
            <dt>{ru.periodCompare}</dt>
            <dd>{rangeText(previous)}</dd>
          </>
        )}
        <dt>{ru.filters}</dt>
        <dd>{description}</dd>
        <dt>{ru.formula}</dt>
        <dd>{meta.formula}</dd>
        <dt>{ru.vat}</dt>
        <dd>{meta.vat}</dd>
        <dt>{ru.source}</dt>
        <dd>{meta.source}</dd>
        <dt>{ru.lastUpload}</dt>
        <dd>{momentText(meta.updated_at, meta.timezone)}</dd>
        <dt>{ru.latestSnapshot}</dt>
        <dd>
          {momentText(meta.generated_at, meta.timezone)} · {meta.timezone}
        </dd>
        <dt>{ru.coverage}</dt>
        <dd>
          {meta.complete && meta.previous_complete !== false ? (
            <>
              <Check size={14} /> {ru.complete}
            </>
          ) : (
            coverageLabel(meta)
          )}
        </dd>
        {!!meta.missing_days?.length && (
          <>
            <dt>{ru.missing}</dt>
            <dd>{meta.missing_days.join(", ")}</dd>
          </>
        )}
        {!!meta.previous_missing_days?.length && (
          <>
            <dt>{ru.missingPrevious}</dt>
            <dd>{meta.previous_missing_days.join(", ")}</dd>
          </>
        )}
      </dl>
    </details>
  );
}
