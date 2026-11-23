import { useState } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ChartSpline,
  ChevronRight,
  Download,
  MapPin,
  Package,
  Sparkles,
  Store,
  Table2,
} from "lucide-react";
import { coverageLabel, Details, Notice } from "./common";
import {
  dateText,
  dayRanges,
  momentText,
  money,
  num,
  pct,
  query,
  rangeText,
} from "./lib";
import { pilotRu, ru } from "./ru";
import { Trend } from "./Trend";
import type { Datum, Sales, Selection } from "./types";

const metrics = ["net", "sales", "returns", "documents"] as const;

function Delta({
  value,
  invert = false,
}: {
  value: Datum | undefined;
  invert?: boolean;
}) {
  if (value == null) return <span className="no-base">{ru.noBase}</span>;
  const n = Number(value),
    Icon = n >= 0 ? ArrowUpRight : ArrowDownLeft;
  return (
    <span
      className={
        "delta " +
        (n === 0 ? "neutral" : (invert ? n < 0 : n > 0) ? "up" : "down")
      }
    >
      <Icon size={13} />
      {pct(value)}
    </span>
  );
}
function Kpis({ sales }: { sales: Sales }) {
  return (
    <div className="kpis">
      {metrics.map((key) => {
        const metric = sales.kpis[key];
        const complete =
          sales.meta.complete && sales.meta.previous_complete !== false;
        return (
          <article className="kpi" key={key}>
            <div className="kpi-head">
              <h2 className="kpi-label">{ru.kpi[key]}</h2>
              {complete && (
                <Delta value={metric.percent} invert={key === "returns"} />
              )}
            </div>
            <strong className="kpi-value">
              {num(metric.value)}{" "}
              {key !== "documents" && <small>{ru.kzt}</small>}
            </strong>
            <div className="kpi-foot" title={ru.compareNote}>
              {complete ? (
                <>
                  <b>
                    {Number(metric.delta) > 0 ? "+" : ""}
                    {key === "documents"
                      ? num(metric.delta)
                      : money(metric.delta)}
                  </b>
                  <span>{ru.vs}</span>
                </>
              ) : (
                coverageLabel(sales.meta)
              )}
            </div>
          </article>
        );
      })}
    </div>
  );
}
function TableLink({
  url,
  label = ru.export,
}: {
  url: string;
  label?: string;
}) {
  return (
    <a
      className="icon-btn"
      href={url}
      download
      aria-label={label}
      title={label}
    >
      <Download size={16} />
    </a>
  );
}
export function SalesHeader({ onAsk }: { onAsk: () => void }) {
  return (
    <div className="page-title">
      <div>
        <h1>{ru.overview}</h1>
      </div>
      <button className="button primary" onClick={onAsk}>
        <Sparkles size={16} />
        {ru.askAction}
      </button>
    </div>
  );
}
export function SalesPage({
  sales,
  selection,
  change,
  base,
}: {
  sales: Sales;
  selection: Selection;
  change: (v: Selection) => void;
  base: string;
}) {
  const [view, setView] = useState("chart"),
    [allProducts, showProducts] = useState(false),
    [compare, setCompare] = useState(false);
  const exportUrl = (table: string) =>
    base +
    "/sales.csv?" +
    query(selection) +
    "&table=" +
    table +
    (table === "products" && allProducts ? "&product_view=all" : "");
  const rows = sales.products,
    max = Math.max(1, Number(rows[0]?.net ?? 0));
  const coverage = coverageLabel(sales.meta);
  return (
    <>
      {base === "/api/live" &&
        (sales.meta.lag_days != null ||
          sales.meta.latest_confirmed_day === null) && (
          <p className="sync-note">
            {sales.meta.latest_confirmed_day ? (
              <>
                {pilotRu.lastDay} ·{" "}
                {dateText(sales.meta.latest_confirmed_day, true)}
                {" · "}
                {pilotRu.lag} {sales.meta.lag_days} {ru.daySingular}
              </>
            ) : (
              pilotRu.noDay
            )}
            <a className="text-btn" href="#/live/source">
              {ru.connectionLink}
            </a>
          </p>
        )}
      {(!sales.meta.complete || sales.meta.previous_complete === false) && (
        <Notice>
          <strong>{coverage}.</strong>{" "}
          {coverage === ru.incompleteComparison
            ? ru.incompleteComparisonHelp
            : ru.incompleteHelp}
          {!!sales.meta.missing_days.length && (
            <div>
              {pilotRu.missed} · {dayRanges(sales.meta.missing_days)}
            </div>
          )}
          {!!sales.meta.previous_missing_days?.length && (
            <div>
              {ru.missingPrevious} ·{" "}
              {dayRanges(sales.meta.previous_missing_days)}
            </div>
          )}
        </Notice>
      )}
      <Kpis sales={sales} />
      <section className="panel trend-panel">
        <div className="panel-head">
          <div>
            <h2>{ru.chart}</h2>
            <strong className="chart-total">
              {money(sales.kpis.net.value)}
            </strong>
            <div className="muted subline">{rangeText(sales.range)}</div>
          </div>
          <div className="segment">
            {[
              ["chart", ChartSpline, ru.chartView],
              ["table", Table2, ru.tableView],
            ].map(([k, icon, label]) => {
              const Icon = icon as typeof Table2;
              return (
                <button
                  key={String(k)}
                  aria-label={String(label)}
                  aria-pressed={view === k}
                  onClick={() => setView(String(k))}
                >
                  <Icon size={14} />
                  <span>{String(label)}</span>
                </button>
              );
            })}
          </div>
        </div>
        {view === "chart" ? (
          <>
            <div className="legend">
              <span>
                <i />
                {ru.current}
              </span>
              <button
                type="button"
                aria-pressed={compare}
                title={rangeText(sales.previous_range)}
                onClick={() => setCompare(!compare)}
              >
                <i />
                {ru.previous}
              </button>
            </div>
            <Trend days={sales.daily} compare={compare} />
          </>
        ) : (
          <div className="table-scroll day-table">
            <table>
              <thead>
                <tr>
                  {[
                    ru.date,
                    ru.metric.sales,
                    ru.metric.returns,
                    ru.metric.net,
                    ru.kpi.documents,
                  ].map((t) => (
                    <th key={t}>{t}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sales.daily.map((d) => (
                  <tr key={d.date}>
                    <td>{dateText(d.date, true)}</td>
                    <td>{money(d.sales)}</td>
                    <td>{money(d.returns)}</td>
                    <td>{money(d.net)}</td>
                    <td>{num(d.documents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="chart-footer">
          <span>
            <i className="status-dot" />
            {sales.meta.updated_at
              ? ru.freshness +
                " " +
                momentText(sales.meta.updated_at, sales.meta.timezone)
              : ru.na}
          </span>
          <div>
            <Details
              meta={sales.meta}
              range={sales.range}
              previous={sales.previous_range}
              filters={sales.filters}
              options={sales.options}
            />
            <TableLink url={exportUrl("daily")} label={ru.chartExport} />
          </div>
        </div>
      </section>
      <div className="split">
        <section className="panel list-panel">
          <div className="panel-head">
            <div>
              <div className="heading-with-icon">
                <span className="section-icon">
                  <Store size={17} />
                </span>
                <h2>{ru.locations}</h2>
              </div>
            </div>
            <TableLink url={exportUrl("points")} />
          </div>
          <div className="table-scroll">
            <table className="points">
              <thead>
                <tr>
                  <th>{ru.point}</th>
                  <th>{ru.metric.net}</th>
                  <th>{ru.change}</th>
                </tr>
              </thead>
              <tbody>
                {sales.points.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <button
                        className="point-btn"
                        onClick={() => change({ ...selection, point: r.id })}
                        title={ru.drill}
                      >
                        <span className="place-icon">
                          <MapPin size={14} />
                        </span>
                        <span>{r.name}</span>
                        <ChevronRight size={12} />
                      </button>
                    </td>
                    <td>{money(r.net)}</td>
                    <td>
                      {sales.meta.complete &&
                      sales.meta.previous_complete !== false ? (
                        <div className="table-delta">
                          <span>
                            {Number(r.delta) > 0 ? "+" : ""}
                            {money(r.delta)}
                          </span>
                          <Delta value={r.percent} />
                        </div>
                      ) : (
                        ru.dash
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!sales.points.length && (
            <div className="empty-mini">
              {ru.empty} {ru.emptyFilters}
            </div>
          )}
        </section>
        <section className="panel list-panel">
          <div className="panel-head">
            <div className="heading-with-icon">
              <span className="section-icon">
                <Package size={17} />
              </span>
              <h2>{selection.point ? ru.storeProducts : ru.ranking}</h2>
            </div>
            <TableLink url={exportUrl("products")} />
          </div>
          <div className="rank-list">
            {(allProducts ? rows : rows.slice(0, 5)).map((r, i) => (
              <div className="rank-row" key={r.id}>
                <span className="rank">{String(i + 1).padStart(2, "0")}</span>
                <div className="rank-track">
                  <strong>{r.name}</strong>
                  <div>
                    <i
                      style={{
                        width: (100 * Math.max(0, Number(r.net))) / max + "%",
                      }}
                    />
                  </div>
                </div>
                <b>{money(r.net)}</b>
              </div>
            ))}
          </div>
          {!rows.length ? (
            <div className="empty-mini">{ru.empty}</div>
          ) : (
            rows.length > 5 && (
              <button
                className="list-toggle"
                onClick={() => showProducts(!allProducts)}
              >
                {allProducts ? ru.less : ru.list + " (" + rows.length + ")"}
                <ChevronRight size={13} />
              </button>
            )
          )}
        </section>
      </div>
    </>
  );
}
