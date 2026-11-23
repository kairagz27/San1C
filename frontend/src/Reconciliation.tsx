import { useRef, useState, type FormEvent } from "react";
import { Bookmark, FileCheck2, FileSpreadsheet } from "lucide-react";
import { Feedback, Notice } from "./common";
import {
  api,
  closedFortnight,
  dateText,
  dayRanges,
  HttpError,
  momentText,
  money,
  rangeText,
  useResource,
} from "./lib";
import { pilotRu as t, ru } from "./ru";
import type {
  Named,
  ReconcileResult,
  ReconcileRow,
  ReconcileSummary,
  ReconcileValues,
} from "./types";

type RequestData = {
  start: string;
  end: string;
  point: string | null;
  report_name: string;
  csv: string;
};
type Scope = keyof typeof t.scopes;
type Metric = keyof ReconcileValues;

function errorText(e: unknown) {
  if (e instanceof HttpError && e.detail && /[А-Яа-яЁё]/.test(e.detail))
    return e.detail;
  return e instanceof HttpError && e.status === 422
    ? t.badCsv
    : ru.networkError;
}

function Preview({ result }: { result: ReconcileResult }) {
  const [scope, setScope] = useState<Scope>("total");
  const [metric, setMetric] = useState<Metric>("net");
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [day, setDay] = useState("");
  const rows = scope === "total" ? [result.total] : result[scope];
  const filtered = rows.filter(
    (r) =>
      (!onlyDiff || r.status !== "matched") &&
      (scope !== "rows" || !day || r.date === day),
  );
  const title = (r: ReconcileRow) =>
    [r.date && dateText(r.date, true), r.point_name || r.point_id]
      .filter(Boolean)
      .join(" · ") || t.scopes.total;
  function inspect(date?: string | null) {
    setScope("rows");
    setDay(date || "");
    setOnlyDiff(!date);
  }
  return (
    <div className="recon-result" aria-live="polite">
      <h3>{result.report_name}</h3>
      <div className="recon-meta">
        <span className={"result-status " + result.status}>
          {t.statuses[result.status]}
        </span>
        <span>
          {rangeText(result)} ·{" "}
          {result.point
            ? result.points.find((p) => p.point_id === result.point)
                ?.point_name || result.point
            : ru.allPoints}
        </span>
      </div>
      {!!result.missing_days.length && (
        <p>
          {t.missed} · {dayRanges(result.missing_days)}
        </p>
      )}
      {!!result.unmapped_points?.length && (
        <p>
          {t.unmapped} · {result.unmapped_points.join(", ")}
        </p>
      )}
      {result.status === "incomplete" && (
        <p className="muted">{t.partialHint}</p>
      )}
      <div className="recon-totals table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t.scopes.total}</th>
              <th>{t.expected}</th>
              <th>{t.actual}</th>
              <th>{t.delta}</th>
            </tr>
          </thead>
          <tbody>
            {(["sales", "returns", "net"] as const).map((m) => (
              <tr key={m}>
                <td>{ru.metric[m]}</td>
                <td>{money(result.total.expected?.[m])}</td>
                <td>{money(result.total.actual?.[m])}</td>
                <td>{money(result.total.delta?.[m])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">{t.deltaFormula}</p>
      {result.status !== "matched" && (
        <button type="button" className="button" onClick={() => inspect()}>
          {t.showRows}
        </button>
      )}
      <div className="segment recon-scope" aria-label={ru.detail}>
        {(["total", "days", "points", "rows"] as const).map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={scope === s}
            onClick={() => {
              setScope(s);
              setDay("");
              setOnlyDiff(false);
            }}
          >
            {t.scopes[s]}
          </button>
        ))}
      </div>
      {scope !== "total" && (
        <>
          <div className="recon-filters">
            <label>
              {t.valuesLabel}
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value as Metric)}
              >
                {(["net", "sales", "returns"] as const).map((m) => (
                  <option key={m} value={m}>
                    {ru.metric[m]}
                  </option>
                ))}
              </select>
            </label>
            {scope === "rows" && (
              <label>
                {t.rowDate}
                <select value={day} onChange={(e) => setDay(e.target.value)}>
                  <option value="">{t.allDays}</option>
                  {result.days.map(
                    (r) =>
                      r.date && (
                        <option key={r.date} value={r.date}>
                          {dateText(r.date, true)}
                        </option>
                      ),
                  )}
                </select>
              </label>
            )}
            <label className="check-label">
              <input
                type="checkbox"
                checked={onlyDiff}
                onChange={(e) => setOnlyDiff(e.target.checked)}
              />
              {t.mismatchOnly}
            </label>
          </div>
          <div className="table-scroll recon-detail">
            <table>
              <thead>
                <tr>
                  <th>{t.scopes[scope]}</th>
                  <th>{t.expected}</th>
                  <th>{t.actual}</th>
                  <th>{t.delta}</th>
                  <th>{ru.status}</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={JSON.stringify([r.date, r.point_id])}>
                    <td>
                      {scope === "days" && r.date ? (
                        <button
                          type="button"
                          className="text-btn"
                          aria-label={t.openDay + " " + r.date}
                          onClick={() => inspect(r.date)}
                        >
                          {title(r)}
                        </button>
                      ) : (
                        title(r)
                      )}
                    </td>
                    <td>{money(r.expected?.[metric])}</td>
                    <td>{money(r.actual?.[metric])}</td>
                    <td>{money(r.delta?.[metric])}</td>
                    <td>{t.statuses[r.status]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!filtered.length && <p>{t.noRows}</p>}
          </div>
        </>
      )}
      <details className="recon-format">
        <summary>{ru.reportInfo}</summary>
        <dl className="facts">
          {[
            [t.company, result.company],
            [
              ru.profile,
              [result.profile_id, result.configuration, result.release]
                .filter(Boolean)
                .join(" · "),
            ],
            [ru.timezone, result.timezone],
            [ru.vat, result.tax_basis],
            [t.controlHash, result.control_sha256],
          ].map(
            ([label, value]) =>
              value && (
                <div className="fact-pair" key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ),
          )}
        </dl>
      </details>
    </div>
  );
}

export function Reconciliation({
  base,
  zone,
  revision,
}: {
  base: string;
  zone: string;
  revision: number;
}) {
  const [dates, setDates] = useState(() => closedFortnight(zone));
  const [point, setPoint] = useState(""),
    [name, setName] = useState(""),
    [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ReconcileResult>(),
    [draft, setDraft] = useState<RequestData>();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  const [historyRev, updateHistory] = useState(0);
  const endpoint = base + "/reconciliation";
  const history = useResource<ReconcileSummary[]>(
    endpoint,
    revision + historyRev,
  );
  const options = useResource<{ points: Named[] }>(base + "/options", revision);
  const requestNumber = useRef(0);
  function reset() {
    ++requestNumber.current;
    setResult(undefined);
    setDraft(undefined);
    setSaved(false);
    setError("");
    setBusy(false);
  }
  async function preview(e: FormEvent) {
    e.preventDefault();
    reset();
    if (!file) {
      setError(t.noCsv);
      return;
    }
    if (file.size > 1_024_003) {
      setError(t.fileLimit);
      return;
    }
    if (!name.trim()) {
      setError(t.requiredName);
      return;
    }
    setBusy(true);
    const number = requestNumber.current;
    try {
      let csv: string;
      try {
        csv = new TextDecoder("utf-8", { fatal: true }).decode(
          await file.arrayBuffer(),
        );
      } catch {
        throw new Error(t.readFailed);
      }
      if (Array.from(csv).length > 256_000) {
        if (number === requestNumber.current) setError(t.fileLimit);
        return;
      }
      const body = {
        ...dates,
        point: point || null,
        report_name: name.trim(),
        csv,
      };
      const value = await api<ReconcileResult>(endpoint + "/preview", body);
      if (number === requestNumber.current) {
        setResult(value);
        setDraft(body);
      }
    } catch (e) {
      if (number === requestNumber.current)
        setError(
          e instanceof Error && e.message === t.readFailed
            ? t.readFailed
            : errorText(e),
        );
    } finally {
      if (number === requestNumber.current) setBusy(false);
    }
  }
  async function save() {
    if (!draft || busy) return;
    setBusy(true);
    setError("");
    const number = requestNumber.current;
    try {
      const value = await api<ReconcileResult>(endpoint + "/save", draft);
      if (number !== requestNumber.current) return;
      setResult(value);
      setDraft(undefined);
      setSaved(true);
      updateHistory((n) => n + 1);
    } catch (e) {
      if (number === requestNumber.current) setError(errorText(e));
    } finally {
      if (number === requestNumber.current) setBusy(false);
    }
  }
  async function open(id: string | number) {
    reset();
    setBusy(true);
    const number = requestNumber.current;
    try {
      const value = await api<ReconcileResult>(
        endpoint + "/" + encodeURIComponent(String(id)),
      );
      if (number === requestNumber.current) setResult(value);
    } catch (e) {
      if (number === requestNumber.current) setError(errorText(e));
    } finally {
      if (number === requestNumber.current) setBusy(false);
    }
  }
  return (
    <section className="panel source-section recon">
      <div className="heading-with-icon">
        <FileSpreadsheet size={18} />
        <h2>{t.reconcile}</h2>
      </div>
      <form className="recon-form" onSubmit={preview}>
        <label>
          {t.reportName}
          <input
            required
            maxLength={200}
            value={name}
            onChange={(e) => {
              reset();
              setName(e.target.value);
            }}
          />
        </label>
        <div className="date-editor">
          <label>
            {ru.start}
            <input
              type="date"
              required
              value={dates.start}
              max={dates.end}
              onChange={(e) => {
                reset();
                setDates({ ...dates, start: e.target.value });
              }}
            />
          </label>
          <label>
            {ru.end}
            <input
              type="date"
              required
              min={dates.start}
              value={dates.end}
              onChange={(e) => {
                reset();
                setDates({ ...dates, end: e.target.value });
              }}
            />
          </label>
          <button
            type="button"
            className="button"
            onClick={() => {
              reset();
              setDates(closedFortnight(zone));
            }}
          >
            {t.firstUpload}
          </button>
          <label>
            {t.rowPoint}
            <select
              value={point}
              onChange={(e) => {
                reset();
                setPoint(e.target.value);
              }}
            >
              <option value="">{ru.allPoints}</option>
              {options.data?.points.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {options.error && (
          <Feedback error={options.error} retry={options.retry} />
        )}
        <p className="muted">{t.categoryBasis}</p>
        <label>
          {t.chooseCsv}
          <input
            type="file"
            required
            accept=".csv,text/csv"
            onChange={(e) => {
              reset();
              setFile(e.target.files?.[0] ?? null);
            }}
          />
        </label>
        <details className="recon-format">
          <summary>{t.format}</summary>
          <p>{t.formatHint}</p>
          <code>{t.formatHeader}</code>
          <h4>{t.ids}</h4>
          {(options.data?.points.length ?? 0) > 0 ? (
            <ul>
              {options.data?.points.map((p) => (
                <li key={p.id}>
                  {p.name} · <code>{p.id}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t.noIds}</p>
          )}
        </details>
        <button className="button primary" disabled={busy}>
          <FileCheck2 size={16} />
          {t.run}
        </button>
      </form>
      {busy && <p role="status">{t.busy}</p>}
      {error && <Notice>{error}</Notice>}
      {result && (
        <div className="recon-review">
          <p className="muted">{draft ? t.preview : t.opened}</p>
          <Preview
            key={JSON.stringify([
              result.id,
              result.report_name,
              result.start,
              result.end,
              result.status,
            ])}
            result={result}
          />
          {draft && (
            <button
              className="button primary"
              type="button"
              onClick={save}
              disabled={busy}
            >
              <Bookmark size={15} />
              {t.save}
            </button>
          )}
          {saved && (
            <div className="success" role="status">
              {t.saved}
            </div>
          )}
        </div>
      )}
      <div className="recon-history">
        <h3>{t.history}</h3>
        {history.error ? (
          <Feedback error={history.error} retry={history.retry} />
        ) : !history.data ? (
          <Feedback />
        ) : history.data.length === 0 ? (
          <p className="muted">{t.noHistory}</p>
        ) : (
          <ul>
            {history.data.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  className="text-btn"
                  disabled={busy || r.id == null}
                  onClick={() => r.id != null && open(r.id)}
                >
                  {r.report_name} · {rangeText(r)}
                </button>
                <span>
                  {t.statuses[r.status]}
                  {r.created_at && " · " + momentText(r.created_at, zone)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
