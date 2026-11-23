import { useState } from "react";
import {
  Activity,
  ArrowUpRight,
  CalendarCheck,
  ChevronDown,
  Clock3,
  FileCode2,
  ShieldCheck,
} from "lucide-react";
import type { Role, Source as SourceData } from "./types";
import {
  closedFortnight,
  dateText,
  dayRanges,
  momentText,
  rangeText,
  useResource,
} from "./lib";
import { Feedback } from "./common";
import { pilotRu as t, ru } from "./ru";
import { Reconciliation } from "./Reconciliation";
import { SourceControls } from "./SourceControls";
const nameFor = (map: Record<string, string>, v: string) => map[v] ?? v;
export function SourcePage({
  base,
  revision,
  role,
}: {
  base: string;
  revision: number;
  role: Role;
}) {
  const [custom, setCustom] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState("");
  const [tab, setTab] = useState<"overview" | "reconcile">("overview");
  const source = useResource<SourceData>(base + "/source" + custom, revision);
  const s = source.data;
  if (!s)
    return (
      <Feedback
        error={source.error}
        retry={() => {
          setCustom("");
          source.retry();
        }}
      />
    );
  const c = s.coverage;
  const attempt = s.last_attempt ?? s.logs[0];
  function queryRange(from: string, to: string) {
    setStart(from);
    setEnd(to);
    setCustom("?" + new URLSearchParams({ start: from, end: to }));
  }
  return (
    <>
      {s.mode === "live" && (
        <div className="segment source-tabs">
          <button
            type="button"
            aria-pressed={tab === "overview"}
            onClick={() => setTab("overview")}
          >
            {t.sourceTab}
          </button>
          <button
            type="button"
            aria-pressed={tab === "reconcile"}
            onClick={() => setTab("reconcile")}
          >
            {t.reconcile}
          </button>
        </div>
      )}
      {s.mode === "live" && tab === "reconcile" ? (
        <Reconciliation base={base} revision={revision} zone={s.timezone} />
      ) : (
        <>
          <div className="source-kpis">
            {[
              [
                ShieldCheck,
                ru.status,
                s.mode === "live" && s.connection?.enabled === false
                  ? t.disabled
                  : nameFor(ru.statuses, s.status),
              ],
              [CalendarCheck, ru.daysConfirmed, c.confirmed + " / " + c.total],
              [
                Clock3,
                ru.lastUpload,
                momentText(s.last_success_at, s.timezone),
              ],
            ].map(([icon, label, value]) => {
              const Icon = icon as typeof Activity;
              return (
                <div className="panel fact-tile" key={String(label)}>
                  <div className="kpi-label">
                    <Icon size={17} />
                    {String(label)}
                  </div>
                  <strong>{String(value)}</strong>
                </div>
              );
            })}
          </div>
          {s.mode === "live" && (
            <section className="panel source-section connection-facts">
              <h2>{ru.connection}</h2>
              <dl className="facts">
                {s.company_name && (
                  <>
                    <dt>{t.company}</dt>
                    <dd>{s.company_name}</dd>
                  </>
                )}
                {s.connection && (
                  <>
                    <dt>{ru.status}</dt>
                    <dd>{s.connection.enabled ? t.active : t.disabled}</dd>
                    <dt>{t.check}</dt>
                    <dd>
                      {s.connection.last_check_at
                        ? [
                            momentText(s.connection.last_check_at, s.timezone),
                            s.connection.last_check_status &&
                              nameFor(
                                t.checkStatuses,
                                s.connection.last_check_status,
                              ),
                            s.connection.message,
                          ]
                            .filter(Boolean)
                            .join(" · ")
                        : t.noCheck}
                    </dd>
                  </>
                )}
                <dt>{t.lastAttempt}</dt>
                <dd>
                  {attempt
                    ? [
                        momentText(attempt.at, s.timezone),
                        attempt.business_date &&
                          dateText(attempt.business_date, true),
                        nameFor(ru.syncCodes, attempt.code),
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : t.noAttempt}
                </dd>
                <dt>{t.lastDay}</dt>
                <dd>
                  {s.latest_confirmed_day || c.last_confirmed
                    ? dateText(
                        (s.latest_confirmed_day || c.last_confirmed)!,
                        true,
                      )
                    : t.noDay}
                </dd>
                <dt>{t.lag}</dt>
                <dd>
                  {s.lag_days == null
                    ? ru.dash
                    : s.lag_days + " " + ru.daySingular}
                </dd>
              </dl>
              <p className="muted">{t.checkDetails}</p>
            </section>
          )}
          <section className="panel source-section">
            <div className="heading-with-icon">
              <span className="section-icon">
                <CalendarCheck size={17} />
              </span>
              <h2>{ru.sourceCoverage}</h2>
            </div>
            <p className="muted">
              {rangeText(c)} · {s.timezone}
            </p>
            <form
              className="date-editor"
              onSubmit={(e) => {
                e.preventDefault();
                queryRange(start || c.start, end || c.end);
              }}
            >
              <label>
                {ru.start}
                <input
                  type="date"
                  required
                  value={start || c.start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <label>
                {ru.end}
                <input
                  type="date"
                  min={start || c.start}
                  required
                  value={end || c.end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </label>
              <button className="button">{ru.apply}</button>
              <button
                type="button"
                className="text-btn"
                onClick={() => {
                  const r = closedFortnight(s.timezone);
                  queryRange(r.start, r.end);
                }}
              >
                {t.firstUpload}
              </button>
            </form>
            <div className="day-grid">
              {c.days.map((d) => (
                <span
                  className={"day " + d.status}
                  key={d.date}
                  title={
                    dateText(d.date, true) +
                    " · " +
                    nameFor(ru.dayStatus, d.status)
                  }
                  aria-label={d.date + " " + nameFor(ru.dayStatus, d.status)}
                >
                  {Number(d.date.slice(-2))}
                </span>
              ))}
            </div>
            <dl className="facts">
              <dt>{t.missed}</dt>
              <dd>{dayRanges(c.missing) || t.none}</dd>
              <dt>{t.emptyDays}</dt>
              <dd>
                {dayRanges(
                  c.days.filter((d) => d.status === "empty").map((d) => d.date),
                ) || t.none}
              </dd>
            </dl>
            {s.mode === "live" && (
              <details className="repeat-guide">
                <summary>{t.repeat}</summary>
                <p>{t.repeatSteps}</p>
              </details>
            )}
            <div className="coverage-key">
              {Object.entries(ru.dayStatus).map(([id, n]) => (
                <span key={id}>
                  <i className={"day " + id} />
                  {n}
                </span>
              ))}
            </div>
            {c.first_confirmed && c.last_confirmed && (
              <p className="muted">
                {ru.confirmed} ·{" "}
                {rangeText({ start: c.first_confirmed, end: c.last_confirmed })}
              </p>
            )}
          </section>
          <div className="split">
            <section className="panel source-section">
              <h2>{ru.profile}</h2>
              <h3>{s.profile.name}</h3>
              <p className="muted">
                {[s.profile.configuration, s.profile.release, s.profile.status]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <p>{s.tax_basis}</p>
              <p>
                {ru.latestSnapshot} ·{" "}
                {momentText(s.last_generated_at, s.timezone)} · {s.timezone}
              </p>
              {(s.mode === "demo" || role === "admin") &&
                s.profile.query_template && (
                  <details className="template">
                    <summary>
                      <FileCode2 size={15} />
                      {ru.template}
                      <ChevronDown size={14} />
                    </summary>
                    <pre>{s.profile.query_template}</pre>
                  </details>
                )}
            </section>
            <section className="panel source-section">
              <h2>{ru.instructions}</h2>
              <ol>
                {ru.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <a
                className="text-btn"
                href="/guide/1c"
                target="_blank"
                rel="noreferrer"
              >
                {ru.assembly}
                <ArrowUpRight size={14} />
              </a>
            </section>
          </div>
          {s.mode === "live" && role === "admin" && s.connection && (
            <SourceControls
              base={base}
              enabled={s.connection.enabled}
              refresh={source.retry}
            />
          )}
          <section className="panel list-panel">
            <div className="panel-head">
              <h2>{ru.journal}</h2>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    {[ru.time, ru.date, ru.status, ru.rows].map((t) => (
                      <th key={t}>{t}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {s.logs.map((l, i) => (
                    <tr key={i}>
                      <td>{momentText(l.at, s.timezone)}</td>
                      <td>{l.business_date ?? ru.dash}</td>
                      <td>{nameFor(ru.syncCodes, l.code)}</td>
                      <td>{l.rows ?? ru.dash}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!s.logs.length && <p className="empty-mini">{ru.noLogs}</p>}
            </div>
          </section>
        </>
      )}
    </>
  );
}
