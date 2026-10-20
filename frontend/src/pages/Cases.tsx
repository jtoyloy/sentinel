import { useState } from "react";
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
export default function Cases() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0),
    [selected, setSelected] = useState(""),
    [selectedSignals, setSignals] = useState<string[]>([]),
    [selectedAlerts, setAlerts] = useState<string[]>([]);
  const detail = useResource<Row>(
      selected ? `/investigations/${selected}` : "/overview",
      revision,
    ),
    signals = useResource<Row[]>("/signals?limit=1000", revision),
    alerts = useResource<Row[]>("/alerts?limit=1000", revision);
  const change = () => setRevision((v) => v + 1);
  return (
    <>
      <PagedTable
        path="/investigations"
        title="Cross-domain investigations"
        caption="Durable cases link immutable trade evidence with communication, restriction and data signals."
        revision={revision}
        columns={[
          { key: "title" },
          { key: "priority", render: (r) => <Badge value={r.priority} /> },
          { key: "status", render: (r) => <Badge value={r.status} /> },
          { key: "owner" },
          { key: "updated_at", render: (r) => when(r.updated_at) },
        ]}
        onSelect={(r) => setSelected(r.id)}
      />
      {user?.role !== "VIEWER" && (
        <Form
          title="Open an investigation"
          caption="Select real persisted findings to preserve a cross-domain evidence trail."
          fields={[
            { name: "title", label: "Case title", maxLength: 200 },
            {
              name: "priority",
              label: "Priority",
              options: ["MEDIUM", "HIGH", "CRITICAL", "LOW"],
            },
            {
              name: "owner",
              label: "Assigned investigator",
              value: user?.username,
              maxLength: 100,
            },
            {
              name: "summary",
              label: "Investigation summary",
              type: "textarea",
            },
          ]}
          submit="Create investigation"
          onSubmit={async (v) => {
            const result = await api("/investigations", {
              ...v,
              signal_ids: selectedSignals,
              alert_ids: selectedAlerts,
            });
            setSignals([]);
            setAlerts([]);
            change();
            setSelected(result.id);
            return result;
          }}
        >
          <div className="evidence-picker">
            <Notice error={signals.error || alerts.error} />
            <label>
              Linked compliance signals
              <select
                multiple
                aria-label="Linked compliance signals"
                value={selectedSignals}
                onChange={(e) =>
                  setSignals(
                    Array.from(e.target.selectedOptions, (x) => x.value),
                  )
                }
              >
                {signals.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.domain} · {s.title} · {s.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Linked trade alerts
              <select
                multiple
                aria-label="Linked trade alerts"
                value={selectedAlerts}
                onChange={(e) =>
                  setAlerts(
                    Array.from(e.target.selectedOptions, (x) => x.value),
                  )
                }
              >
                {alerts.data?.map((a) => (
                  <option key={a.alert_id} value={a.alert_id}>
                    {a.instrument_id} · {a.alert_type} ·{" "}
                    {a.alert_id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">
              Use Command / Control to select multiple findings.{" "}
              {selectedSignals.length} signals and {selectedAlerts.length}{" "}
              alerts selected.
            </p>
          </div>
        </Form>
      )}
      {selected && (
        <Drawer title="Investigation workspace" onClose={() => setSelected("")}>
          <Notice error={detail.error} />
          {detail.loading ? (
            <p>Loading investigation…</p>
          ) : (
            detail.data && (
              <>
                <Badge value={detail.data.status} />
                <h3 className="spaced">{detail.data.title}</h3>
                <Facts
                  row={detail.data}
                  keys={["id", "priority", "owner", "created_at", "updated_at"]}
                />
                <p className="panel-copy">{detail.data.summary}</p>
                <h3>Linked signals</h3>
                {detail.data.signals?.map((s: Row) => (
                  <article className="evidence-block" key={s.id}>
                    <Badge value={s.domain} />
                    <Badge value={s.severity} />
                    <h3>{s.title}</h3>
                    <p>{s.explanation}</p>
                    <Json value={s.evidence} />
                  </article>
                ))}
                {!detail.data.signals?.length && (
                  <p className="muted">No linked compliance signals.</p>
                )}
                <h3 className="spaced">Linked trade alerts</h3>
                <Table
                  rows={detail.data.alerts}
                  columns={[
                    { key: "instrument_id" },
                    { key: "alert_type" },
                    {
                      key: "severity",
                      render: (r) => <Badge value={r.severity} />,
                    },
                  ]}
                  onSelect={(r) =>
                    window.dispatchEvent(
                      new CustomEvent("sentinel-trade-detail", { detail: r }),
                    )
                  }
                />
                <h3 className="spaced">Case timeline</h3>
                <ol className="audit-list">
                  {detail.data.timeline?.map((t: Row) => (
                    <li key={t.id}>
                      <div>
                        <strong>
                          {t.action} · {t.actor}
                        </strong>
                        <time>{when(t.created_at)}</time>
                      </div>
                      <p>{t.notes}</p>
                    </li>
                  ))}
                </ol>
                {user?.role !== "VIEWER" && (
                  <Form
                    key={detail.data.status}
                    title="Record investigation action"
                    fields={[
                      {
                        name: "action",
                        label: "Action",
                        options:
                          detail.data.status === "CLOSED"
                            ? ["REOPEN"]
                            : ["NOTE", "ESCALATE", "CLOSE"],
                      },
                      {
                        name: "notes",
                        label: "Evidence reviewed and rationale",
                        type: "textarea",
                      },
                    ]}
                    submit="Save action"
                    onSubmit={async (v) => {
                      const result = await api(
                        `/investigations/${selected}/actions`,
                        v,
                      );
                      change();
                      return result;
                    }}
                  />
                )}
              </>
            )
          )}
        </Drawer>
      )}
      <Panel title="Investigation scope">
        <p className="panel-copy">
          Signals describe suspected activity requiring human review. Case
          actions and findings are attributed to the authenticated investigator
          and retained in the audit history.
        </p>
      </Panel>
    </>
  );
}
