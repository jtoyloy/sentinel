import { FormEvent, useEffect, useState } from "react";
import { AuthContext, User } from "./auth";
import { api, Row } from "./platformApi";
import { Drawer, Json, Notice } from "./ui";
import TradeSurveillance from "./TradeSurveillance";
import Overview, { Operations } from "./pages/Overview";
import { Employees, Communications } from "./pages/People";
import Restrictions from "./pages/Restrictions";
import Cases from "./pages/Cases";
import Data, { Reports } from "./pages/Data";
import { AlertDetail } from "./components/AlertDetail";
import { Alert } from "./types";
import "./styles.css";
const views = [
  [
    "overview",
    "◈",
    "Compliance overview",
    "FIRMWIDE OVERSIGHT",
    "Monitor risk across your firm. Follow the evidence.",
  ],
  [
    "employees",
    "♙",
    "Employee management",
    "IDENTITY & PERSONNEL",
    "Connect HR identities, trading accounts and profile history.",
  ],
  [
    "communications",
    "✉",
    "Communications",
    "COMMUNICATION SURVEILLANCE",
    "Preserve messages, inspect rule matches and correlate trading activity.",
  ],
  [
    "trades",
    "▦",
    "Trade surveillance",
    "MARKET INTEGRITY",
    "Detect suspicious trading behavior and investigate source evidence.",
  ],
  [
    "restrictions",
    "⊘",
    "Trading restrictions",
    "POLICY & PERMISSIONS",
    "Enforce versioned trading policies with independent approvals.",
  ],
  [
    "investigations",
    "▤",
    "Investigations",
    "CASE MANAGEMENT",
    "Unify findings, document decisions and escalate investigations.",
  ],
  [
    "data",
    "⇄",
    "Data & reconciliation",
    "DATA OPERATIONS",
    "Normalize broker feeds and resolve execution discrepancies.",
  ],
  [
    "reports",
    "↓",
    "Regulatory reporting",
    "REPORTING & AUDIT",
    "Export synthetic execution records with a verifiable audit trail.",
  ],
  [
    "operations",
    "⌁",
    "Pipeline health",
    "OPERATIONAL RELIABILITY",
    "Inspect live dependencies, delivery state and recovery controls.",
  ],
];
function currentView() {
  const id = window.location.hash.slice(1);
  return views.some((v) => v[0] === id) ? id : "overview";
}
export default function App() {
  const [user, setUser] = useState<User | null>(null),
    [checking, setChecking] = useState(true),
    [sessionError, setSessionError] = useState(""),
    [view, setView] = useState(currentView),
    [raw, setRaw] = useState<Row | null>(null),
    [trade, setTrade] = useState<Alert | null>(null);
  useEffect(() => {
    api<User>("/auth/me")
      .then(setUser)
      .catch((e) => {
        if (!e.message.startsWith("401:")) setSessionError(e.message);
      })
      .finally(() => setChecking(false));
    const expired = () => {
        setUser(null);
        setSessionError("Your session has expired. Sign in again.");
      },
      hash = () => setView(currentView()),
      inspect = (e: Event) => setRaw((e as CustomEvent).detail),
      tradeInspect = (e: Event) => setTrade((e as CustomEvent).detail);
    window.addEventListener("sentinel-session-expired", expired);
    window.addEventListener("hashchange", hash);
    window.addEventListener("sentinel-inspect", inspect);
    window.addEventListener("sentinel-trade-detail", tradeInspect);
    return () => {
      window.removeEventListener("sentinel-session-expired", expired);
      window.removeEventListener("hashchange", hash);
      window.removeEventListener("sentinel-inspect", inspect);
      window.removeEventListener("sentinel-trade-detail", tradeInspect);
    };
  }, []);
  async function logout() {
    try {
      await api("/auth/logout", {});
      setUser(null);
      setSessionError("");
    } catch (e) {
      setSessionError(e instanceof Error ? e.message : String(e));
    }
  }
  if (checking)
    return (
      <div className="login-screen">
        <div className="login-card">
          <Brand />
          <p>Checking authenticated workspace…</p>
        </div>
      </div>
    );
  if (!user)
    return (
      <Login
        initialError={sessionError}
        onLogin={(value) => {
          setUser(value);
          setSessionError("");
        }}
      />
    );
  const meta = views.find((v) => v[0] === view)!;
  return (
    <AuthContext.Provider value={{ user }}>
      <div className="app-shell platform-shell">
        <aside className="sidebar">
          <Brand />
          <div className="workspace-label">COMPLIANCE WORKSPACE</div>
          <nav aria-label="Platform navigation">
            {views.map(([id, icon, name]) => (
              <a
                key={id}
                aria-label={name}
                href={`#${id}`}
                className={`nav-item ${view === id ? "active" : ""}`}
                aria-current={view === id ? "page" : undefined}
              >
                <span>{icon}</span>
                {name}
              </a>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <span className="avatar">
              {user.username.slice(0, 2).toUpperCase()}
            </span>
            <div>
              {user.username}
              <small>{user.role.toLowerCase()} access</small>
            </div>
          </div>
          <button className="logout" onClick={logout}>
            Sign out ↗
          </button>
        </aside>
        <main>
          <header className="topbar">
            <div className="breadcrumb">
              Sentinel <span>/</span> {meta[2]}
            </div>
            <div className="topbar-right">
              <span className="outline-tag">SYNTHETIC DATA ONLY</span>
              <span className="user-role">{user.role}</span>
            </div>
          </header>
          <div className="page-content">
            <Notice error={sessionError} />
            {view !== "trades" && (
              <div className="page-title">
                <div>
                  <p className="eyebrow">{meta[3]}</p>
                  <h1>{meta[2]}</h1>
                  <p className="subtitle">{meta[4]}</p>
                </div>
              </div>
            )}
            {view === "overview" ? (
              <Overview />
            ) : view === "employees" ? (
              <Employees />
            ) : view === "communications" ? (
              <Communications />
            ) : view === "trades" ? (
              <TradeSurveillance />
            ) : view === "restrictions" ? (
              <Restrictions />
            ) : view === "investigations" ? (
              <Cases />
            ) : view === "data" ? (
              <Data />
            ) : view === "reports" ? (
              <Reports />
            ) : (
              <Operations />
            )}
            {view !== "trades" && (
              <footer>
                Sentinel · Institutional compliance{" "}
                <span>
                  Evidence is preserved. Human judgment remains essential.
                </span>
              </footer>
            )}
          </div>
        </main>
      </div>
      {raw && (
        <Drawer title="Persisted evidence record" onClose={() => setRaw(null)}>
          <Json value={raw} title="Inspect complete record" />
        </Drawer>
      )}
      {trade && (
        <AlertDetail
          alert={trade}
          onClose={() => setTrade(null)}
          onUpdated={setTrade}
        />
      )}
    </AuthContext.Provider>
  );
}
function Brand() {
  return (
    <a className="brand" href="#overview">
      <span className="brand-mark">S</span>SENTINEL
      <span className="brand-dot">.</span>
    </a>
  );
}
function Login({
  onLogin,
  initialError,
}: {
  onLogin: (u: User) => void;
  initialError: string;
}) {
  const [error, setError] = useState(initialError),
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const v = Object.fromEntries(new FormData(e.currentTarget));
    try {
      onLogin(await api<User>("/auth/login", v));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-screen">
      <section className="login-story">
        <Brand />
        <p className="eyebrow">INSTITUTIONAL FINANCIAL COMPLIANCE</p>
        <h1>
          Clarity across
          <br />
          every signal.
        </h1>
        <p>
          Connect people, conversations and market activity in a single
          evidence-led surveillance workspace.
        </p>
        <div className="login-capabilities">
          <span>Trade surveillance</span>
          <span>Communication review</span>
          <span>Policy controls</span>
          <span>Compliance investigations</span>
        </div>
        <small>
          Synthetic data exclusively · Versioned evidence · Persistent audit
        </small>
      </section>
      <section className="login-card">
        <p className="eyebrow">SECURE WORKSPACE</p>
        <h2>Sign in to Sentinel</h2>
        <p>Use your provisioned compliance account.</p>
        <form className="login-form" onSubmit={submit}>
          <label>
            Username
            <input
              name="username"
              required
              autoComplete="username"
              maxLength={100}
              autoFocus
            />
          </label>
          <label>
            Password
            <input
              name="password"
              required
              type="password"
              autoComplete="current-password"
              maxLength={1024}
            />
          </label>
          <button className="button primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in →"}
          </button>
        </form>
        <Notice error={error} />
        <p className="login-help">
          Access is assigned by role. Ask your administrator to provision an
          account using the documented setup command.
        </p>
      </section>
    </div>
  );
}
