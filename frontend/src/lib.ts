import { useEffect, useState } from "react";
import { ru } from "./ru";
import type { DateRange, Datum, Selection } from "./types";

export class HttpError extends Error {
  constructor(
    public status: number,
    public detail?: string,
  ) {
    super(String(status));
  }
}
export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const result = await fetch(path, {
    signal,
    credentials: "same-origin",
    cache: "no-store",
    method: body === undefined ? "GET" : "POST",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!result.ok) {
    const error = await result.json().catch(() => null);
    throw new HttpError(
      result.status,
      typeof error?.detail === "string" ? error.detail : undefined,
    );
  }
  return result.json();
}
export function useResource<T>(path: string | null, revision = 0) {
  const [state, setState] = useState<{
    path: string | null;
    data?: T;
    error?: Error;
  }>({ path: null });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setState((prior) =>
      prior.path === path && !prior.error ? prior : { path },
    );
    api<T>(path, undefined, controller.signal)
      .then((data) => setState({ path, data }))
      .catch((error) => {
        if (!controller.signal.aborted) setState({ path, error });
      });
    return () => controller.abort();
  }, [path, revision, attempt]);
  const current = path === state.path ? state : { path };
  return { ...current, retry: () => setAttempt((n) => n + 1) };
}
export function query(selection: Selection) {
  return new URLSearchParams(
    Object.entries(selection).filter(
      ([key, v]) =>
        v && (selection.period === "custom" || !["start", "end"].includes(key)),
    ) as [string, string][],
  ).toString();
}
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
export function num(v: Datum | undefined) {
  if (v == null) return ru.dash;
  if (typeof v === "number") return number.format(v);
  const [whole, fraction = ""] = v.split(".");
  const digits = fraction.replace(/0+$/, "");
  const sign = whole.startsWith("-") ? "−" : "";
  return (
    sign +
    number.format(BigInt(whole.replace(/^-/, ""))) +
    (digits ? "," + digits : "")
  );
}
export function money(v: Datum | undefined) {
  return v == null ? ru.dash : num(v) + " " + ru.kzt;
}
export function pct(v: Datum | undefined) {
  return v == null
    ? ru.noBase
    : (Number(v) > 0 ? "+" : "") +
        new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(
          Number(v),
        ) +
        "%";
}
export function compact(v: number) {
  const n = Math.abs(v);
  if (n >= 1e6) return num(v / 1e6) + "\u00a0" + ru.unitMillion;
  if (n >= 1e3) return num(v / 1e3) + "\u00a0" + ru.unitThousand;
  return num(v);
}
export function dateText(iso: string, year = false) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    ...(year ? { year: "numeric" as const } : {}),
    timeZone: "UTC",
  })
    .format(new Date(iso + "T12:00:00Z"))
    .replace(/\s?г\.$/, "");
}
export function rangeText(r: DateRange) {
  return (
    dateText(r.start, r.start.slice(0, 4) !== r.end.slice(0, 4)) +
    " — " +
    dateText(r.end, true)
  );
}
export function dayRanges(days: string[]) {
  const ranges: DateRange[] = [];
  for (const day of [...new Set(days)].sort()) {
    const last = ranges.at(-1);
    if (last && Date.parse(day) - Date.parse(last.end) === 86_400_000)
      last.end = day;
    else ranges.push({ start: day, end: day });
  }
  return ranges
    .map((r) => (r.start === r.end ? dateText(r.start, true) : rangeText(r)))
    .join(", ");
}

export function closedFortnight(zone: string): DateRange {
  const iso = new Intl.DateTimeFormat("sv-SE", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const last = new Date(iso + "T00:00:00Z");
  last.setUTCDate(last.getUTCDate() - ((last.getUTCDay() + 6) % 7) - 1);
  const first = new Date(last);
  first.setUTCDate(first.getUTCDate() - 13);
  return {
    start: first.toISOString().slice(0, 10),
    end: last.toISOString().slice(0, 10),
  };
}
export function momentText(iso: string | null, tz: string) {
  return iso
    ? new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: tz,
      }).format(new Date(iso))
    : ru.na;
}
export async function exportPlan(base: string, plan: unknown) {
  const response = await fetch(base + "/report.csv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan }),
  });
  if (!response.ok) throw new HttpError(response.status);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "san1c-report.csv";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
