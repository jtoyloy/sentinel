import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";
import { Alert, TradingEvent } from "../types";
import {
  AuditEntry,
  fetchAlertById,
  fetchEvidence,
  fetchHistory,
  updateAlert,
} from "../hooks/useApi";
const label = (value: string) => value.toLowerCase().replace(/_/g, " ");
const date = (value: string) => new Date(value).toLocaleString();

export function AlertDetail({
  alert: initial,
  onClose,
  onUpdated,
}: {
  alert: Alert;
  onClose: () => void;
  onUpdated: (alert: Alert) => void;
}) {
  const { user } = useAuth();
  const [alert, setAlert] = useState(initial);
  const [evidence, setEvidence] = useState<TradingEvent[]>([]);
  const [history, setHistory] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState(initial.status);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");
  const [reload, setReload] = useState(0);
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchAlertById(initial.alert_id),
      fetchEvidence(initial.alert_id),
      fetchHistory(initial.alert_id),
    ])
      .then(([value, events, entries]) => {
        if (!cancelled) {
          setAlert(value);
          setStatus(value.status);
          setEvidence(events);
          setHistory(entries);
          setError("");
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(reason.message || String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initial.alert_id, reload]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    dialog.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const targets = dialog.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input, select, textarea, summary, [tabindex="0"]',
        );
        if (!targets?.length) return;
        const first = targets[0],
          last = targets[targets.length - 1];
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === dialog.current)
        ) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", keyboard);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", keyboard);
      previous?.focus();
    };
  }, [onClose]);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaved("");
    setError("");
    try {
      const value = await updateAlert(
        alert.alert_id,
        status,
        user?.username || "",
        notes.trim(),
      );
      setAlert(value);
      setStatus(value.status);
      setNotes("");
      setSaved("Investigation update saved.");
      onUpdated(value);
      try {
        setHistory(await fetchHistory(alert.alert_id));
      } catch {
        setError(
          "Update saved, but audit history could not be refreshed. Reopen this alert to retry.",
        );
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }
  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="drawer"
        ref={dialog}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="detail-title"
      >
        <div className="drawer-header">
          <div>
            <p className="eyebrow">INVESTIGATION</p>
            <h2 id="detail-title">
              {label(alert.alert_type)}{" "}
              <span
                className={`badge severity-${alert.severity.toLowerCase()}`}
              >
                {label(alert.severity)}
              </span>
            </h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close investigation"
          >
            ×
          </button>
        </div>
        <div className="drawer-content">
          <div className="case-meta">
            <div>
              <span>Instrument</span>
              <strong>{alert.instrument_id}</strong>
            </div>
            <div>
              <span>Status</span>
              <strong>{label(alert.status)}</strong>
            </div>
            <div>
              <span>Detected</span>
              <strong>{date(alert.created_at)}</strong>
            </div>
          </div>
          <div className="case-id">ALERT {alert.alert_id}</div>
          {error && (
            <div className="notice error" role="alert">
              {error}
              <button onClick={() => setReload((v) => v + 1)}>
                Reload details
              </button>
            </div>
          )}
          <section className="detail-section">
            <h3>Detection summary</h3>
            <p>{alert.description}</p>
            <div className="explanation">{alert.explanation}</div>
            <p className="muted detail-note">
              Rule based suspicion requires investigation; it does not establish
              intent.
            </p>
          </section>
          <section className="detail-section">
            <div className="evidence-heading">
              <h3>Underlying evidence</h3>
              <span className="outline-tag">{evidence.length} EVENTS</span>
            </div>
            <p className="muted detail-note">
              Event window: {date(alert.time_window_start)} —{" "}
              {date(alert.time_window_end)}
            </p>
            {loading ? (
              <p>Loading persisted evidence…</p>
            ) : (
              <>
                <div className="table-scroll evidence-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Event time</th>
                        <th>Event / side</th>
                        <th>Quantity / price</th>
                        <th>Account / order</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evidence.map((event) => (
                        <tr key={event.event_id}>
                          <td className="nowrap">{date(event.timestamp)}</td>
                          <td>
                            <strong>{event.event_type}</strong>
                            <span
                              className={event.side === "BUY" ? "buy" : "sell"}
                            >
                              {event.side} · {event.instrument_id}
                            </span>
                          </td>
                          <td>
                            <strong>{event.quantity.toLocaleString()}</strong>
                            <span>
                              {Number(event.price).toLocaleString(undefined, {
                                minimumFractionDigits: 2,
                                maximumFractionDigits: 6,
                              })}
                            </span>
                          </td>
                          <td>
                            <strong className="break-id">
                              {event.account_id}
                            </strong>
                            <span className="break-id">{event.order_id}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!evidence.length && (
                  <p className="muted">No underlying events returned.</p>
                )}
                <details className="raw-evidence">
                  <summary>Inspect complete records and rule evidence</summary>
                  <pre>
                    {JSON.stringify(
                      { rule_evidence: alert.evidence, events: evidence },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </>
            )}
          </section>
          {user?.role !== "VIEWER" && (
            <section className="detail-section">
              <h3>Investigator disposition</h3>
              <form className="disposition-form" onSubmit={save}>
                <div className="form-grid">
                  <label>
                    Status
                    <select
                      value={status}
                      onChange={(e) =>
                        setStatus(e.target.value as Alert["status"])
                      }
                    >
                      {[
                        "OPEN",
                        "INVESTIGATING",
                        "RESOLVED",
                        "FALSE_POSITIVE",
                      ].map((v) => (
                        <option value={v} key={v}>
                          {label(v)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Investigator
                    <input readOnly value={user?.username || ""} />
                  </label>
                </div>
                <label>
                  Investigation notes
                  <textarea
                    required
                    maxLength={4000}
                    rows={3}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="Document the evidence reviewed and reason for this disposition…"
                  />
                </label>
                <button
                  className="button primary"
                  disabled={saving || !notes.trim()}
                >
                  {saving ? "Saving…" : "Save disposition"}
                </button>
                {saved && (
                  <p className="saved-message" role="status">
                    {saved}
                  </p>
                )}
              </form>
            </section>
          )}
          <section className="detail-section">
            <h3>Audit history</h3>
            {loading ? (
              <p className="muted">Loading history…</p>
            ) : history.length ? (
              <ol className="audit-list">
                {history.map((entry, index) => (
                  <li key={entry.id || entry.audit_id || index}>
                    <div>
                      <strong>{entry.actor}</strong>
                      <time>{date(entry.created_at)}</time>
                    </div>
                    <span className="audit-transition">
                      {label(entry.previous_status)} → {label(entry.new_status)}
                    </span>
                    <p>{entry.notes}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">No investigator updates recorded.</p>
            )}
          </section>
        </div>
      </section>
    </div>
  );
}
