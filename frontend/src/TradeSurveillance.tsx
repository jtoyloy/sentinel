import { useCallback, useEffect, useRef, useState } from "react";
import { AlertDetail } from "./components/AlertDetail";
import { useWebSocket } from "./hooks/useWebSocket";
import { fetchAlerts, fetchStats, replayScenario, Stats } from "./hooks/useApi";
import { Alert } from "./types";
import { useAuth } from "./auth";

export const label = (value: string) => value.toLowerCase().replace(/_/g, " ");
export const date = (value: string) =>
  new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
const pageSize = 15;

export default function TradeSurveillance() {
  const { user } = useAuth();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [selected, setSelected] = useState<Alert | null>(null);
  const closeAlert = useCallback(() => setSelected(null), []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [instrument, setInstrument] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [scenario, setScenario] = useState("mixed");
  const [seed, setSeed] = useState("42");
  const [baseTime, setBaseTime] = useState("2026-10-20T14:30:00Z");
  const [replaying, setReplaying] = useState(false);
  const [replayMessage, setReplayMessage] = useState("");
  const [replayError, setReplayError] = useState("");
  const { isConnected, revision } = useWebSocket();
  const requestNumber = useRef(0);
  useEffect(() => {
    const current = ++requestNumber.current;
    setLoading(true);
    Promise.all([
      fetchAlerts({
        status,
        alert_type: type,
        instrument_id: instrument.trim(),
        limit: pageSize + 1,
        offset,
      }),
      fetchStats(),
    ])
      .then(([rows, counts]) => {
        if (current !== requestNumber.current) return;
        setAlerts(rows.slice(0, pageSize));
        setHasMore(rows.length > pageSize);
        setStats(counts);
        setError("");
        setLastUpdated(new Date());
      })
      .catch((reason) => {
        if (current === requestNumber.current)
          setError(String(reason.message || reason));
      })
      .finally(() => {
        if (current === requestNumber.current) setLoading(false);
      });
    return () => {
      requestNumber.current++;
    };
  }, [status, type, instrument, offset, revision, refresh]);
  // REST polling also catches changes made by other investigators while the socket is unavailable.
  useEffect(() => {
    const timer = setInterval(() => setRefresh((v) => v + 1), 10000);
    return () => clearInterval(timer);
  }, []);
  async function runReplay(event: React.FormEvent) {
    event.preventDefault();
    setReplayError("");
    setReplayMessage("");
    const parsedSeed = Number(seed);
    if (
      !Number.isSafeInteger(parsedSeed) ||
      !baseTime.match(/(Z|[+-]\d{2}:\d{2})$/) ||
      Number.isNaN(Date.parse(baseTime))
    ) {
      setReplayError(
        "Enter an integer seed and an ISO timestamp with a timezone.",
      );
      return;
    }
    setReplaying(true);
    try {
      const result = await replayScenario(
        scenario,
        parsedSeed,
        new Date(baseTime).toISOString(),
      );
      setReplayMessage(
        `${result.events_generated.toLocaleString()} events persisted · run ${result.run_id}. Detection continues asynchronously.`,
      );
      setRefresh((v) => v + 1);
    } catch (reason) {
      setReplayError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setReplaying(false);
    }
  }
  return (
    <div className="trade-embedded">
      <div className="trade-content">
        <div className="page-content">
          <div className="page-title">
            <div>
              <p className="eyebrow">MARKET INTEGRITY</p>
              <h1>Trade surveillance</h1>
              <p className="subtitle">
                Monitor suspicious activity. Follow the evidence.
              </p>
            </div>
            <button
              className="button"
              onClick={() => setRefresh((v) => v + 1)}
              disabled={loading}
            >
              ↻ Refresh
            </button>
          </div>
          <div className={`connection ${isConnected ? "connected" : ""}`}>
            <i />
            {isConnected ? "Live alert stream" : "Reconnecting alert stream"}
          </div>
          <div className="metrics">
            {[
              [
                "Events ingested",
                stats?.events,
                "Persisted trading events",
                "neutral",
              ],
              [
                "Total alerts",
                stats?.alerts,
                "Across all detection rules",
                "neutral",
              ],
              [
                "Open alerts",
                stats?.open_alerts,
                "Awaiting investigation",
                "amber",
              ],
              [
                "Critical alerts",
                stats?.critical_alerts,
                "Highest severity · active alerts",
                "red",
              ],
            ].map(([title, value, caption, color]) => (
              <div className="metric" key={String(title)}>
                <div className="metric-label">{title}</div>
                <strong className={String(color)}>
                  {typeof value === "number" ? value.toLocaleString() : "—"}
                </strong>
                <small>{caption}</small>
              </div>
            ))}
          </div>
          <section className="panel" id="alerts">
            <div className="section-heading">
              <div>
                <h2>
                  Alert queue <span className="live-tag">LIVE</span>
                </h2>
                <p>Rule based signals requiring investigator review</p>
              </div>
              <span className="timestamp">
                {lastUpdated
                  ? `Updated ${lastUpdated.toLocaleTimeString()}`
                  : "Connecting to surveillance service"}
              </span>
            </div>
            <div className="filters">
              <label>
                Status
                <select
                  value={status}
                  onChange={(e) => {
                    setStatus(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All statuses</option>
                  {["OPEN", "INVESTIGATING", "RESOLVED", "FALSE_POSITIVE"].map(
                    (v) => (
                      <option key={v} value={v}>
                        {label(v)}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label>
                Detection rule
                <select
                  value={type}
                  onChange={(e) => {
                    setType(e.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All rules</option>
                  <option value="WASH_TRADE">Suspected wash trade</option>
                  <option value="SPOOFING">Suspected spoofing</option>
                </select>
              </label>
              <label className="instrument-filter">
                Instrument
                <input
                  value={instrument}
                  placeholder="Filter exact symbol…"
                  onChange={(e) => {
                    setInstrument(e.target.value);
                    setOffset(0);
                  }}
                />
              </label>
              <span className="filter-caption">
                {loading
                  ? "Updating queue…"
                  : `${alerts.length} alerts on this page`}
              </span>
            </div>
            {error && (
              <div role="alert" className="notice error">
                Unable to refresh: {error}
                <button onClick={() => setRefresh((v) => v + 1)}>
                  Try again
                </button>
              </div>
            )}
            <div className="table-scroll">
              <table className="alert-table">
                <thead>
                  <tr>
                    <th>Detection / instrument</th>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Detected</th>
                    <th>Evidence</th>
                    <th>
                      <span className="sr-only">Inspect</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((alert) => (
                    <tr key={alert.alert_id} onClick={() => setSelected(alert)}>
                      <td>
                        <button
                          className="row-title"
                          onClick={() => setSelected(alert)}
                        >
                          {label(alert.alert_type)}
                          <span>
                            {alert.instrument_id} <i>·</i>{" "}
                            {alert.alert_id.slice(0, 8)}
                          </span>
                        </button>
                      </td>
                      <td>
                        <span
                          className={`badge severity-${alert.severity.toLowerCase()}`}
                        >
                          <i />
                          {label(alert.severity)}
                        </span>
                      </td>
                      <td>
                        <span
                          className={`badge status-${alert.status.toLowerCase()}`}
                        >
                          {label(alert.status)}
                        </span>
                      </td>
                      <td className="muted nowrap">{date(alert.created_at)}</td>
                      <td className="muted">
                        {alert.evidence?.event_ids?.length ?? 0} events
                      </td>
                      <td className="row-arrow">↗</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!alerts.length && (
              <div className="empty">
                <div className="empty-icon">⌕</div>
                <h3>
                  {loading
                    ? "Loading surveillance alerts"
                    : "No alerts in this queue"}
                </h3>
                <p>
                  {loading
                    ? "Reading persisted alerts from the surveillance service."
                    : "Adjust the filters or replay a scenario to exercise the detection pipeline."}
                </p>
              </div>
            )}
            <div className="pagination">
              <span>
                {alerts.length
                  ? `Showing ${offset + 1}–${offset + alerts.length}`
                  : "No results"}{" "}
                · newest first
              </span>
              <div>
                <button
                  className="button small"
                  disabled={offset === 0 || loading}
                  onClick={() => setOffset((v) => Math.max(0, v - pageSize))}
                >
                  ← Previous
                </button>
                <button
                  className="button small"
                  disabled={!hasMore || loading}
                  onClick={() => setOffset((v) => v + pageSize)}
                >
                  Next →
                </button>
              </div>
            </div>
          </section>
          {user?.role !== "VIEWER" && (
            <section className="panel replay-panel" id="replay">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">DETECTION LAB</p>
                  <h2>Deterministic scenario replay</h2>
                  <p>
                    Generate synthetic trading events and run them through the
                    live ingestion pipeline.
                  </p>
                </div>
                <span className="outline-tag">SYNTHETIC DATA</span>
              </div>
              <form className="replay-form" onSubmit={runReplay}>
                <label>
                  Scenario
                  <select
                    aria-label="Scenario"
                    value={scenario}
                    onChange={(e) => setScenario(e.target.value)}
                  >
                    <option value="mixed">Mixed suspicious activity</option>
                    <option value="wash_trade">Suspected wash trade</option>
                    <option value="spoofing">Suspected spoofing</option>
                    <option value="normal">Normal trading activity</option>
                  </select>
                </label>
                <label className="seed-field">
                  Seed
                  <input
                    required
                    type="number"
                    step="1"
                    value={seed}
                    onChange={(e) => setSeed(e.target.value)}
                  />
                </label>
                <label className="time-field">
                  Base event time (ISO 8601)
                  <input
                    required
                    value={baseTime}
                    onChange={(e) => setBaseTime(e.target.value)}
                  />
                </label>
                <button className="button primary" disabled={replaying}>
                  {replaying ? "Persisting events…" : "▶ Run replay"}
                </button>
              </form>
              <p className="replay-help">
                Same seed and event time reproduce the scenario; each run
                receives its own account and order identifiers.
              </p>
              {replayMessage && (
                <div className="notice success" role="status">
                  {replayMessage}
                </div>
              )}
              {replayError && (
                <div className="notice error" role="alert">
                  {replayError}
                </div>
              )}
            </section>
          )}
          <footer>
            Sentinel · Trade surveillance{" "}
            <span>
              Signals indicate suspected activity and require human review.
            </span>
          </footer>
        </div>
      </div>
      {selected && (
        <AlertDetail
          key={selected.alert_id}
          alert={selected}
          onClose={closeAlert}
          onUpdated={(value) => {
            setSelected(value);
            setRefresh((v) => v + 1);
          }}
        />
      )}
    </div>
  );
}
