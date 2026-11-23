import { useState, type FormEvent } from "react";
import {
  ArrowDownRight,
  ArrowRight,
  Bookmark,
  BookmarkCheck,
  CalendarDays,
  ChartNoAxesColumnIncreasing,
  Check,
  ChevronDown,
  Download,
  LayoutGrid,
  MessageSquareText,
  Package,
  RotateCcw,
  Send,
  Sparkles,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Details, Feedback, Notice } from "./common";
import {
  api,
  compact,
  dateText,
  exportPlan,
  money,
  num,
  pct,
  rangeText,
} from "./lib";
import { ru } from "./ru";
import { Trend } from "./Trend";
import type { Filters, Named, Report } from "./types";
type Options = { points: Named[]; categories: Named[] };

function ReportView({
  report,
  base,
  options,
}: {
  report: Report;
  base: string;
  options?: Options;
}) {
  const [exportError, setExportError] = useState(false);
  const { plan, meta, rows } = report;
  const compare = plan.comparison !== "none",
    fmt = plan.metric === "documents" ? num : money;
  const plotted = rows.map((row) => ({
    ...row,
    plot:
      (compare ? row.delta : row.value) == null
        ? null
        : Number(compare ? row.delta : row.value),
  }));
  return (
    <div className="report-body">
      <div className="answer-date">
        <CalendarDays size={14} />
        {rangeText(report.range)}{" "}
        {report.previous_range && (
          <span>
            {" "}
            · {ru.compare.toLowerCase()} {rangeText(report.previous_range)}
          </span>
        )}
      </div>
      <h3 className="answer-copy">{report.answer}</h3>
      {plan.view === "line" && (
        <Trend days={rows.map((r) => ({ date: r.id, net: r.value }))} />
      )}
      {plan.view === "bar" && plotted.some((v) => v.plot != null) && (
        <div
          className="horizontal-bars"
          style={{ height: Math.max(120, plotted.length * 34 + 35) }}
        >
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <BarChart
              layout="vertical"
              data={plotted}
              margin={{ left: 0, right: 18, top: 5, bottom: 0 }}
            >
              <CartesianGrid
                horizontal={false}
                stroke="#303035"
                strokeDasharray="3 5"
              />
              <XAxis
                type="number"
                tickFormatter={compact}
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 10, fill: "#adadb4" }}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={118}
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: "#c3c3ca" }}
              />
              <Tooltip
                cursor={{ fill: "#ffffff08" }}
                contentStyle={{
                  background: "#232326",
                  border: "1px solid #434349",
                  borderRadius: 7,
                }}
                itemStyle={{ color: "#eeeeef" }}
                formatter={(v) => [
                  fmt(Number(v)),
                  compare ? ru.change : ru.metric[plan.metric],
                ]}
              />
              <Bar
                dataKey="plot"
                maxBarSize={13}
                radius={3}
                isAnimationActive={false}
              >
                {plotted.map((r) => (
                  <Cell
                    key={r.id}
                    fill={
                      compare
                        ? (r.plot ?? 0) < 0
                          ? "#db7f8e"
                          : "#68b7a1"
                        : "#bdbdc4"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="result-tools">
        <span>{ru.tableView}</span>
        <button
          className="text-btn"
          onClick={() =>
            exportPlan(base, plan)
              .then(() => setExportError(false))
              .catch(() => setExportError(true))
          }
        >
          <Download size={14} />
          {ru.export}
        </button>
      </div>
      {exportError && <Notice>{ru.networkError}</Notice>}
      <div className="table-scroll report-table">
        <table>
          <thead>
            <tr>
              <th>{ru.group[plan.group_by]}</th>
              <th>{ru.metric[plan.metric]}</th>
              {compare && (
                <>
                  <th>{ru.previous}</th>
                  <th>{ru.change}, ₸</th>
                  <th>%</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  {plan.group_by === "day" ? dateText(r.id, true) : r.name}
                </td>
                <td>{fmt(r.value)}</td>
                {compare && (
                  <>
                    <td>{fmt(r.previous)}</td>
                    <td className={Number(r.delta) < 0 ? "negative-text" : ""}>
                      {money(r.delta)}
                    </td>
                    <td>{meta.complete ? pct(r.percent) : ru.dash}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <p className="empty-mini">{ru.empty}</p>}
      </div>
      <Details
        meta={meta}
        range={report.range}
        previous={report.previous_range}
        filters={plan.filters}
        options={options}
      />
    </div>
  );
}
export function AskPage({
  base,
  filters,
  options,
  onSaved,
  openWidgets,
}: {
  base: string;
  filters: Filters;
  options?: Options;
  onSaved: () => void;
  openWidgets: () => void;
}) {
  const [question, setQuestion] = useState(""),
    [report, setReport] = useState<Report>(),
    [clarification, setClarification] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(false),
    [saveError, setSaveError] = useState(false),
    [saved, setSaved] = useState(false),
    [saving, setSaving] = useState(false);
  async function ask(text: string) {
    if (!text.trim() || busy) return;
    setQuestion(text);
    setBusy(true);
    setError(false);
    setReport(undefined);
    setClarification("");
    setSaved(false);
    setSaveError(false);
    try {
      const result = await api<Report | { clarification: string }>(
        base + "/ask",
        {
          question: text.trim(),
          filters: {
            point: filters.point ?? null,
            category: filters.category ?? null,
          },
        },
      );
      if ("clarification" in result) setClarification(result.clarification);
      else setReport(result);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (!report || saving) return;
    setSaving(true);
    setSaveError(false);
    try {
      await api(base + "/widgets", {
        question: report.question,
        plan: report.plan,
      });
      setSaved(true);
      onSaved();
    } catch {
      setSaveError(true);
    } finally {
      setSaving(false);
    }
  }
  const icons = [
    ChartNoAxesColumnIncreasing,
    ArrowDownRight,
    Package,
    RotateCcw,
  ];
  return (
    <>
      <section className="panel composer">
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void ask(question);
          }}
        >
          <label htmlFor="question">
            <span className="section-icon">
              <Sparkles size={17} />
            </span>
            {ru.askInput}
          </label>
          <textarea
            id="question"
            required
            maxLength={500}
            placeholder={ru.askPlaceholder}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={3}
          />
          <button
            className="button primary"
            disabled={busy || !question.trim()}
          >
            <Send size={15} />
            {ru.askSend}
          </button>
        </form>
      </section>
      {busy && <Feedback />}
      {error && (
        <Feedback error={new Error()} retry={() => void ask(question)} />
      )}
      {clarification && <Notice>{clarification}</Notice>}
      {report && (
        <section className="panel answer-panel">
          <div className="panel-head">
            <div className="heading-with-icon">
              <span className="section-icon">
                <MessageSquareText size={17} />
              </span>
              <h2>{ru.answer}</h2>
            </div>
            {saved ? (
              <button className="button" onClick={openWidgets}>
                <BookmarkCheck size={15} />
                {ru.viewWidgets}
              </button>
            ) : (
              <button className="button" onClick={save} disabled={saving}>
                <Bookmark size={15} />
                {ru.save}
              </button>
            )}
          </div>
          {saved && (
            <div className="success" role="status">
              <Check size={15} />
              {ru.saved}
            </div>
          )}
          {saveError && <Notice>{ru.networkError}</Notice>}
          <ReportView report={report} base={base} options={options} />
        </section>
      )}
      <section className="examples">
        <h2>{ru.examples}</h2>
        <div className="example-grid">
          {ru.examplesList.map((q, i) => {
            const Icon = icons[i];
            return (
              <button key={q} disabled={busy} onClick={() => void ask(q)}>
                <span className="section-icon">
                  <Icon size={18} />
                </span>
                <span>
                  <strong>{ru.exampleTitles[i]}</strong>
                  <small>{q}</small>
                </span>
                <ArrowRight size={15} />
              </button>
            );
          })}
        </div>
      </section>
    </>
  );
}
export function Widgets({
  reports,
  base,
  onAsk,
  options,
  compactView = false,
}: {
  reports: Report[];
  base: string;
  onAsk: () => void;
  options?: Options;
  compactView?: boolean;
}) {
  if (!reports.length)
    return compactView ? null : (
      <div className="panel empty">
        <span className="empty-icon">
          <LayoutGrid size={28} />
        </span>
        <h2>{ru.noWidgets}</h2>
        <p>{ru.widgetGuide}</p>
        <button className="button primary" onClick={onAsk}>
          <Sparkles size={16} />
          {ru.askAction}
        </button>
      </div>
    );
  return (
    <section className="widget-list">
      {compactView && <h2>{ru.dashboardWidgets}</h2>}
      {reports.map((r) => (
        <details
          key={r.id}
          className="panel widget"
          open={compactView ? undefined : true}
        >
          <summary>
            <span className="section-icon">
              <Bookmark size={17} />
            </span>
            <span>{r.question}</span>
            <ChevronDown size={16} />
          </summary>
          <ReportView report={r} base={base} options={options} />
        </details>
      ))}
    </section>
  );
}
