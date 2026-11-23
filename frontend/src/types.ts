export type Mode = "demo" | "live";
export type Role = "owner" | "admin";
export type Page = "sales" | "ask" | "widgets" | "source";
export type Metric = "net" | "sales" | "returns" | "documents";
export type Period =
  | "last_30_days"
  | "this_month"
  | "last_month"
  | "last_week"
  | "custom";
export type DateRange = { start: string; end: string };
export type Filters = { point?: string | null; category?: string | null };
export type Selection = Filters & {
  period: Period;
  start?: string;
  end?: string;
};
export type Datum = string | number | null;
export type Totals = Record<Metric, Datum>;
export type Change = {
  value: Datum;
  previous: Datum;
  delta: Datum;
  percent: Datum;
};
export type Named = { id: string; name: string };
export type SummaryRow = Named & Totals & Partial<Change>;
export type Daily = Totals & {
  date: string;
  covered: boolean;
  previous: Datum;
};
export type Meta = {
  lag_days?: number | null;
  latest_confirmed_day?: string | null;
  complete: boolean;
  current_complete?: boolean;
  previous_complete?: boolean;
  missing_days: string[];
  previous_missing_days?: string[];
  source: string;
  updated_at: string | null;
  generated_at: string | null;
  formula: string;
  timezone: string;
  vat: string;
};
export type Sales = {
  range: DateRange;
  previous_range: DateRange;
  filters: Filters;
  options: { points: Named[]; categories: Named[] };
  kpis: Record<Metric, Change>;
  daily: Daily[];
  points: SummaryRow[];
  products: SummaryRow[];
  meta: Meta;
};
export type Plan = {
  metric: Metric;
  period: Period;
  group_by: "day" | "point" | "product" | "category";
  filters: Filters;
  comparison: "none" | "previous_week";
  view: "line" | "bar" | "table";
  limit: number | null;
};
export type Report = {
  id?: string | number;
  question: string;
  plan: Plan;
  answer: string;
  rows: (Named & Change)[];
  range: DateRange;
  previous_range: DateRange | null;
  meta: Meta;
};
export type Source = {
  company_name?: string | null;
  connection?: {
    enabled: boolean;
    last_check_at: string | null;
    last_check_status: string | null;
    message: string | null;
  };
  last_attempt?: SyncLog | null;
  lag_days?: number | null;
  latest_confirmed_day?: string | null;
  mode: Mode;
  timezone: string;
  tax_basis: string;
  status: string;
  last_success_at: string | null;
  last_generated_at: string | null;
  profile: {
    id: string;
    name: string;
    configuration: string;
    release: string;
    status: string;
    tax_basis: string;
    query_template: string | null;
  };
  coverage: DateRange & {
    confirmed: number;
    total: number;
    missing: string[];
    first_confirmed: string | null;
    last_confirmed: string | null;
    days: { date: string; status: string; rows: number | null }[];
  };
  logs: SyncLog[];
};
export type SyncLog = {
  at: string;
  business_date: string | null;
  code: string;
  rows: number | null;
};
export type ReconcileStatus = "matched" | "mismatch" | "incomplete";
export type ReconcileValues = { sales: string; returns: string; net: string };
export type ReconcileRow = {
  date?: string | null;
  point_id?: string | null;
  point_name?: string | null;
  status: ReconcileStatus;
  expected: ReconcileValues | null;
  actual: ReconcileValues | null;
  delta: ReconcileValues | null;
};
export type ReconcileSummary = {
  id?: string | number;
  created_at?: string;
  start: string;
  end: string;
  report_name: string;
  point: string | null;
  status: ReconcileStatus;
};
export type ReconcileResult = ReconcileSummary & {
  rows: ReconcileRow[];
  days: ReconcileRow[];
  points: ReconcileRow[];
  total: ReconcileRow;
  missing_days: string[];
  unmapped_points?: string[];
  profile_id?: string;
  configuration?: string;
  release?: string;
  company?: string;
  timezone?: string;
  tax_basis?: string;
  control_sha256?: string;
};
