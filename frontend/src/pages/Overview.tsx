import { useEffect, useState } from "react";
import { useAuth } from "../auth";
import { api, Row, when } from "../platformApi";
import {
  Badge,
  Drawer,
  Facts,
  Form,
  Json,
  Notice,
  PagedTable,
  Panel,
  Table,
  useResource,
} from "../ui";
export default function Overview() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0),
    [selected, setSelected] = useState<Row | null>(null),
    [domain, setDomain] = useState(""),
    [status, setStatus] = useState("");
  const r = useResource<Row>("/overview", revision);
  useEffect(() => {
    const t = setInterval(() => setRevision((v) => v + 1), 10000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <Notice error={r.error} />
      <div className="metrics platform-metrics">
        {[
          ["employees", "Employee profiles"],
          ["communications", "Communications"],
          ["trade_events", "Trading events"],
          ["trade_alerts", "Trade alerts"],
          ["signals", "Cross-domain signals"],
          ["open_cases", "Active investigations"],
        ].map(([k, name]) => (
          <div className="metric" key={k}>
            <div className="metric-label">{name}</div>
            <strong>{r.data?.[k]?.toLocaleString() ?? "—"}</strong>
            <small>Persisted system total</small>
          </div>
        ))}
      </div>
      <div className="filters standalone">
        <label>
          Signal domain
          <select value={domain} onChange={(e) => setDomain(e.target.value)}>
            <option value="">All domains</option>
            {["COMMUNICATION", "RESTRICTION", "DATA"].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label>
          Signal status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {["OPEN", "INVESTIGATING", "RESOLVED", "FALSE_POSITIVE"].map(
              (v) => (
                <option key={v}>{v}</option>
              ),
            )}
          </select>
        </label>
      </div>
      <PagedTable
        key={domain + status}
        path={`/signals?${new URLSearchParams({ ...(domain ? { domain } : {}), ...(status ? { status } : {}) })}`}
        title="Unified compliance signal queue"
        caption="Communication, trading restriction and reconciliation findings · refreshed every 10 seconds"
        revision={revision}
        columns={[
          { key: "domain", render: (r) => <Badge value={r.domain} /> },
          { key: "title" },
          { key: "severity", render: (r) => <Badge value={r.severity} /> },
          { key: "status", render: (r) => <Badge value={r.status} /> },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={setSelected}
      />
      <Panel
        title="Evidence-led oversight"
        caption="A single persistent platform across people, communications and market activity"
      >
        <div className="domain-summary">
          <div>
            <strong>1. Normalize and enrich</strong>
            <p>
              HR identities and security reference data connect canonical trade
              and communication feeds.
            </p>
          </div>
          <div>
            <strong>2. Detect and explain</strong>
            <p>
              Versioned rules preserve source evidence, precise policy decisions
              and reproducible findings.
            </p>
          </div>
          <div>
            <strong>3. Investigate and escalate</strong>
            <p>
              Link actual findings into cases, record rationale and export
              audited execution reports.
            </p>
          </div>
        </div>
      </Panel>
      {selected && (
        <Drawer title="Compliance signal" onClose={() => setSelected(null)}>
          <Badge value={selected.domain} />
          <Badge value={selected.severity} />
          <h3 className="spaced">{selected.title}</h3>
          <p className="panel-copy">{selected.explanation}</p>
          <Facts row={selected} keys={["id", "rule", "status", "created_at"]} />
          <Json
            value={selected.evidence}
            title="Exact evidence references and rule metrics"
          />
          {user?.role !== "VIEWER" && (
            <Form
              title="Record signal disposition"
              fields={[
                {
                  name: "status",
                  label: "Status",
                  options: [
                    "OPEN",
                    "INVESTIGATING",
                    "RESOLVED",
                    "FALSE_POSITIVE",
                  ],
                  value: selected.status,
                },
                {
                  name: "notes",
                  label: "Review rationale",
                  type: "textarea",
                  maxLength: 4000,
                },
              ]}
              submit="Save disposition"
              onSubmit={async (v) => {
                const result = await api(`/signals/${selected.id}`, v, "PATCH");
                setSelected(result);
                setRevision((n) => n + 1);
                return result;
              }}
            />
          )}
        </Drawer>
      )}
    </>
  );
}
export function Operations() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0);
  const r = useResource<Row>("/operations", revision);
  useEffect(() => {
    const t = setInterval(() => setRevision((v) => v + 1), 5000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <Notice error={r.error} />
      <div className="service-grid">
        {[
          ["postgres", "PostgreSQL"],
          ["redis", "Redis Streams"],
          ["worker_healthy", "Detection worker"],
        ].map(([k, name]) => (
          <div className="metric" key={k}>
            <div className="metric-label">{name}</div>
            <strong className={r.data?.[k] ? "service-up" : "service-down"}>
              {r.loading && !r.data
                ? "Checking…"
                : r.data?.[k]
                  ? "Healthy"
                  : "Unavailable"}
            </strong>
            <small>
              {k === "worker_healthy"
                ? "Heartbeat freshness below 15 seconds"
                : "Current dependency probe"}
            </small>
          </div>
        ))}
      </div>
      <Panel
        title="Pipeline state"
        caption="Real persisted worker state and transport measurements · refreshed every 5 seconds"
      >
        {r.data ? (
          <>
            <Facts
              row={r.data}
              keys={[
                "outbox_pending",
                "stream_length",
                "cursor",
                "events",
                "feed_issue_count",
              ]}
            />
            <h3 className="panel-copy">Worker heartbeat</h3>
            {r.data.worker ? (
              <Facts row={r.data.worker} keys={Object.keys(r.data.worker)} />
            ) : (
              <p className="panel-copy muted">
                No worker heartbeat has been recorded.
              </p>
            )}
            <Json value={r.data} />
          </>
        ) : (
          <p className="panel-copy">Loading operational state…</p>
        )}
      </Panel>
      <Panel
        title="Retained stream processing failures"
        caption="Malformed messages are preserved for diagnosis and do not halt subsequent work."
      >
        <Table
          rows={r.data?.stream_failures}
          columns={[
            { key: "stream_id" },
            { key: "error" },
            { key: "created_at", render: (r) => when(r.created_at) },
          ]}
          onSelect={(row) =>
            window.dispatchEvent(
              new CustomEvent("sentinel-inspect", { detail: row }),
            )
          }
        />
      </Panel>
      {user?.role === "ADMIN" && (
        <Form
          title="Pipeline recovery"
          caption="Republish pending outbox work or rebuild delivery from retained events. Source evidence and audits remain persisted; duplicate detections are idempotent."
          fields={[
            {
              name: "mode",
              label: "Recovery mode",
              options: ["REPUBLISH_PENDING", "REBUILD_STREAM"],
            },
            {
              name: "confirmation",
              label: "Type RECOVER to execute",
              maxLength: 7,
            },
          ]}
          submit="Run recovery"
          onSubmit={async (v) => {
            if (v.confirmation !== "RECOVER")
              throw new Error(
                "Type RECOVER to confirm the selected recovery operation.",
              );
            const result = await api("/operations/recover", { mode: v.mode });
            setRevision((n) => n + 1);
            return result;
          }}
        />
      )}
      <p className="muted">
        Operational health is measured at refresh time; throughput and
        end-to-end freshness require separate measurement.
      </p>
    </>
  );
}
