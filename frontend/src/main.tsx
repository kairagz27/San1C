import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowLeftRight,
  ArrowRight,
  ChartNoAxesColumnIncreasing,
  ChevronDown,
  Database,
  FlaskConical,
  LayoutDashboard,
  LogIn,
  LogOut,
  Menu,
  ShieldCheck,
  Sparkles,
  Store,
  X,
} from "lucide-react";
import "@fontsource-variable/inter/wght.css";
import { Feedback, FilterBar, Notice } from "./common";
import { api, HttpError, query, useResource } from "./lib";
import { AskPage, Widgets } from "./Questions";
import { SalesHeader, SalesPage } from "./Sales";
import { SourcePage } from "./Source";
import { pilotRu, ru } from "./ru";
import type {
  Mode,
  Named,
  Page,
  Report,
  Role,
  Sales,
  Selection,
} from "./types";
import "./styles.css";

const nav = {
  sales: ChartNoAxesColumnIncreasing,
  ask: Sparkles,
  widgets: LayoutDashboard,
  source: Database,
};
function getRoute(): { mode: Mode; page: Page } {
  const [, m, p] = window.location.hash.replace("#", "").split("/");
  return {
    mode: m === "live" ? "live" : "demo",
    page: p && Object.hasOwn(nav, p) ? (p as Page) : "sales",
  };
}
function go(mode: Mode, page: Page) {
  window.location.hash = "/" + mode + "/" + page;
}
function Login({
  configured,
  onLogin,
}: {
  configured: boolean;
  onLogin: () => void;
}) {
  const [password, setPassword] = useState(""),
    [role, setRole] = useState<Role>("owner"),
    [error, setError] = useState(false),
    [busy, setBusy] = useState(false);
  return (
    <section className="panel login">
      <span className="empty-icon">
        <ShieldCheck size={26} />
      </span>
      <h1>{ru.login}</h1>
      <p>{ru.loginHint}</p>
      {configured ? (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError(false);
            try {
              await api("/api/auth/login", { role, password });
              setPassword("");
              onLogin();
            } catch {
              setError(true);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            {pilotRu.role}
            <select
              value={role}
              onChange={(e) => {
                setRole(e.target.value as Role);
                setPassword("");
                setError(false);
              }}
            >
              <option value="owner">{pilotRu.roles.owner}</option>
              <option value="admin">{pilotRu.roles.admin}</option>
            </select>
          </label>
          <label htmlFor="password">{ru.password}</label>
          <input
            id="password"
            type="password"
            value={password}
            required
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && <Notice>{ru.loginFail}</Notice>}
          <button className="button primary" disabled={busy}>
            <LogIn size={16} />
            {ru.signIn}
          </button>
        </form>
      ) : (
        <Notice>{ru.unconfigured}</Notice>
      )}
      <button className="text-btn" onClick={() => go("demo", "sales")}>
        {ru.toDemo}
        <ArrowRight size={14} />
      </button>
    </section>
  );
}
function Workspace({ mode, page }: { mode: Mode; page: Page }) {
  const [selection, change] = useState<Selection>({ period: "last_30_days" });
  const [revision, refresh] = useState(0),
    [drawer, setDrawer] = useState(false),
    [account, setAccount] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const base = "/api/" + mode,
    other = mode === "demo" ? "live" : "demo",
    company = mode === "demo" ? ru.company : ru.realCompany;
  const session = useResource<{
    authenticated: boolean;
    configured: boolean;
    role?: Role | null;
  }>(mode === "live" ? "/api/auth/session" : null, revision);
  const allowed = mode === "demo" || session.data?.authenticated === true;
  const sales = useResource<Sales>(
    allowed && page === "sales" ? base + "/sales?" + query(selection) : null,
    revision,
  );
  const options = useResource<{ points: Named[]; categories: Named[] }>(
    allowed && (page === "sales" || page === "ask" || page === "widgets")
      ? base + "/options"
      : null,
    revision,
  );
  const widgets = useResource<Report[]>(
    allowed && (page === "sales" || page === "widgets")
      ? base + "/widgets"
      : null,
    revision,
  );
  useEffect(() => {
    setDrawer(false);
    setAccount(false);
    document.title = "San1C · " + ru.nav[page];
    window.scrollTo(0, 0);
  }, [page, mode]);
  useEffect(() => {
    const tick = () => {
      if (!document.hidden) refresh((n) => n + 1);
    };
    const timer = setInterval(tick, 60000);
    window.addEventListener("focus", tick);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", tick);
    };
  }, []);
  useEffect(() => {
    if (!drawer && !account) return;
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setAccount(false);
        setDrawer(false);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [drawer, account]);
  useEffect(() => {
    if (
      mode === "live" &&
      [sales.error, options.error, widgets.error].some(
        (e) => e instanceof HttpError && e.status === 401,
      )
    )
      session.retry();
  }, [sales.error, options.error, widgets.error, mode]);
  const goto = (p: Page) => go(mode, p);
  const toOther = () => {
    setDrawer(false);
    setAccount(false);
    go(other, other === "live" ? "source" : "sales");
  };
  const seeAsk = () => goto("ask");
  const opts = options.data ??
    sales.data?.options ?? { points: [], categories: [] };
  const link = (p: Page) => {
    const Icon = nav[p];
    return (
      <a
        key={p}
        href={"#/" + mode + "/" + p}
        aria-current={page === p ? "page" : undefined}
        className={"nav-link " + (page === p ? "active" : "")}
        onClick={() => setDrawer(false)}
      >
        <Icon size={19} />
        <span>{ru.nav[p]}</span>
      </a>
    );
  };
  return (
    <div className="app">
      <a
        className="skip"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        {ru.skip}
      </a>
      {drawer && (
        <button
          className="scrim"
          aria-label={ru.close}
          onClick={() => setDrawer(false)}
        />
      )}
      <aside
        className={"rail " + (drawer ? "opened" : "")}
        aria-label={ru.menu}
      >
        <div className="brand-row">
          <a className="brand" href={"#/" + mode + "/sales"}>
            <img src="/mark.svg" width="34" height="34" alt="" />
            {ru.brand}
          </a>
          <button
            className="rail-close"
            aria-label={ru.close}
            onClick={() => setDrawer(false)}
          >
            <X size={20} />
          </button>
        </div>
        <div className="account-box">
          <button
            className="account-btn"
            onClick={() => setAccount(!account)}
            aria-label={ru.mode}
            aria-expanded={account}
          >
            <span className="avatar">
              <Store size={16} />
            </span>
            <span className="company-name">
              <strong>{company}</strong>
              <small>{mode === "demo" ? ru.demo : ru.live}</small>
            </span>
            <ChevronDown size={13} />
          </button>
          {account && (
            <>
              <button
                className="dismiss"
                aria-label={ru.close}
                onClick={() => setAccount(false)}
              />
              <div className="account-menu">
                <div>
                  <strong>{company}</strong>
                  <small>{pilotRu.roles[session.data?.role ?? "owner"]}</small>
                </div>
                <button onClick={toOther}>
                  <ArrowLeftRight size={15} />
                  {mode === "demo" ? ru.toLive : ru.toDemo}
                </button>
                {mode === "live" && allowed && (
                  <button
                    onClick={async () => {
                      try {
                        await api("/api/auth/logout", {});
                        session.retry();
                        setAccount(false);
                      } catch {
                        setLogoutError(true);
                      }
                    }}
                  >
                    <LogOut size={15} />
                    {ru.signOut}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
        <nav>
          <div className="nav-label">{ru.navigation}</div>
          {(["sales", "ask", "widgets"] as Page[]).map(link)}
          <div className="nav-label second">{ru.settings}</div>
          {link("source")}
        </nav>
        <div className="rail-end">
          <div className="rail-unit">
            <span className="mode-icon">
              {mode === "demo" ? (
                <FlaskConical size={17} />
              ) : (
                <Database size={17} />
              )}
            </span>
            <div>
              <strong>{mode === "demo" ? ru.statuses.demo : ru.live}</strong>
              <small>
                {mode === "demo" ? ru.sourceDemoHint : ru.sourceRealHint}
              </small>
            </div>
          </div>
          <button onClick={toOther}>
            {mode === "demo" ? ru.modeHelp : ru.toDemo}
            <ArrowRight size={14} />
          </button>
        </div>
      </aside>
      <div className="shell">
        <header className="topbar">
          <div className="crumb">
            <button
              className="hamburger icon-btn"
              aria-label={ru.menu}
              aria-expanded={drawer}
              onClick={() => setDrawer(!drawer)}
            >
              <Menu size={20} />
            </button>
            <strong>{ru.nav[page]}</strong>
          </div>
          <div className="top-right">
            <span className={"mode-pill " + (mode === "live" ? "live" : "")}>
              <i />
              {mode === "demo" ? ru.demo : ru.live}
            </span>
          </div>
        </header>
        <main id="main" tabIndex={-1} className="main">
          {logoutError && <Notice>{ru.networkError}</Notice>}
          {!allowed ? (
            session.data ? (
              <Login
                configured={session.data.configured}
                onLogin={() => refresh((n) => n + 1)}
              />
            ) : (
              <Feedback error={session.error} retry={session.retry} />
            )
          ) : (
            <>
              {page === "sales" ? (
                <SalesHeader onAsk={seeAsk} />
              ) : (
                <div className="page-title">
                  <div>
                    <h1>
                      {page === "ask"
                        ? ru.askTitle
                        : page === "widgets"
                          ? ru.widgetsTitle
                          : ru.sourceTitle}
                    </h1>
                    <p>
                      {page === "ask"
                        ? ru.askSub
                        : page === "widgets"
                          ? ru.widgetsSub
                          : ru.sourceSub}
                    </p>
                  </div>
                  {page === "widgets" && (
                    <button className="button primary" onClick={seeAsk}>
                      <Sparkles size={16} />
                      {ru.askAction}
                    </button>
                  )}
                </div>
              )}
              {(page === "ask" || page === "sales") && (
                <FilterBar
                  selection={selection}
                  onChange={change}
                  options={opts}
                  range={sales.data?.range}
                  onlyDimensions={page === "ask"}
                />
              )}
              {page === "sales" &&
                (sales.data ? (
                  <>
                    <SalesPage
                      sales={sales.data}
                      base={base}
                      selection={selection}
                      change={change}
                    />
                    {widgets.data && (
                      <Widgets
                        reports={widgets.data}
                        base={base}
                        onAsk={seeAsk}
                        options={opts}
                        compactView
                      />
                    )}
                  </>
                ) : (
                  <Feedback error={sales.error} retry={sales.retry} />
                ))}
              {page === "ask" && (
                <AskPage
                  key={base + "/" + selection.point + "/" + selection.category}
                  base={base}
                  filters={selection}
                  options={opts}
                  onSaved={() => refresh((n) => n + 1)}
                  openWidgets={() => goto("widgets")}
                />
              )}
              {page === "widgets" &&
                (widgets.data ? (
                  <Widgets
                    reports={widgets.data}
                    base={base}
                    onAsk={seeAsk}
                    options={opts}
                  />
                ) : (
                  <Feedback error={widgets.error} retry={widgets.retry} />
                ))}
              {page === "source" && (
                <SourcePage
                  base={base}
                  revision={revision}
                  role={session.data?.role ?? "owner"}
                />
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}
function App() {
  const [r, setRoute] = useState(getRoute);
  useEffect(() => {
    const track = () => setRoute(getRoute());
    window.addEventListener("hashchange", track);
    return () => window.removeEventListener("hashchange", track);
  }, []);
  return <Workspace key={r.mode} {...r} />;
}
createRoot(document.getElementById("root")!).render(<App />);
