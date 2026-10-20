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
  Table,
  useResource,
} from "../ui";
const seedField = {
  name: "seed",
  label: "Deterministic seed",
  type: "number",
  value: "42",
  min: 0,
  max: 2147483647,
};
const timeField = {
  name: "timestamp",
  label: "Event timestamp (with timezone)",
  value: new Date().toISOString(),
};
export function Employees() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0),
    [search, setSearch] = useState(""),
    [selected, setSelected] = useState("");
  const r = useResource<Row>(
    selected ? `/employees/${encodeURIComponent(selected)}` : "/overview",
    revision,
  );
  const change = () => setRevision((v) => v + 1);
  return (
    <>
      <div className="filters standalone">
        <label>
          Search name, email or employee ID
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search employees…"
          />
        </label>
      </div>
      <PagedTable
        key={search}
        path={`/employees?search=${encodeURIComponent(search)}`}
        title="Employee directory"
        caption="Resolved identities, current HR attributes and trading account coverage"
        revision={revision}
        columns={[
          { key: "employee_id" },
          { key: "full_name" },
          { key: "department" },
          { key: "role" },
          { key: "status", render: (r) => <Badge value={r.status} /> },
          { key: "account_count", title: "Accounts" },
        ]}
        onSelect={(r) => setSelected(r.employee_id)}
      />
      {user?.role !== "VIEWER" && (
        <div className="two-column">
          <Form
            title="Synthetic HR feed"
            caption="Seeded profiles persist with idempotent source keys."
            fields={[
              seedField,
              {
                name: "count",
                label: "Employee count",
                type: "number",
                value: "12",
                min: 1,
                max: 1000,
              },
            ]}
            submit="Import HR profiles"
            onSubmit={async (v) => {
              const result = await api(
                `/employees/simulate?seed=${v.seed}&count=${v.count}`,
                {},
              );
              change();
              return result;
            }}
          />
          <Form
            title="Import HR records"
            caption="Submit a JSON array of HR feed records. Invalid records are retained as quality issues."
            fields={[
              {
                name: "records",
                label: "HR record array (JSON)",
                type: "textarea",
                value: "[]",
                maxLength: 500000,
              },
            ]}
            submit="Validate and ingest"
            onSubmit={async (v) => {
              const result = await api("/employees/feed", {
                records: JSON.parse(v.records),
              });
              change();
              return result;
            }}
          />
        </div>
      )}
      <Form
        title="Identity resolution"
        caption="Resolve a normalized email, exact employee ID or unambiguous account ID."
        fields={[{ name: "identity", label: "Identity", maxLength: 254 }]}
        submit="Resolve identity"
        onSubmit={(v) =>
          api(`/employees/resolve?identity=${encodeURIComponent(v.identity)}`)
        }
      />
      <PagedTable
        path="/employees/quality"
        title="HR data quality"
        revision={revision}
        columns={[
          { key: "source_key" },
          { key: "reason" },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={(r) => {
          setSelected("");
          window.dispatchEvent(
            new CustomEvent("sentinel-inspect", { detail: r }),
          );
        }}
      />
      {selected && (
        <Drawer title="Employee profile" onClose={() => setSelected("")}>
          <Notice error={r.error} />
          {r.loading ? (
            <p>Loading employee…</p>
          ) : (
            r.data && (
              <>
                <h3>{r.data.full_name}</h3>
                <Facts
                  row={r.data}
                  keys={[
                    "employee_id",
                    "email",
                    "department",
                    "role",
                    "status",
                    "manager_id",
                    "updated_at",
                  ]}
                />
                <h3>Trading account mappings</h3>
                <Table
                  rows={r.data.accounts}
                  columns={[{ key: "firm_id" }, { key: "account_id" }]}
                />
                <h3 className="spaced">Immutable HR history</h3>
                {r.data.history?.map((h: Row) => (
                  <article className="evidence-block" key={h.id}>
                    <strong>{when(h.effective_at)}</strong>
                    <p>{h.source_key}</p>
                    <Json value={h.payload} title="Inspect profile change" />
                  </article>
                ))}
              </>
            )
          )}
        </Drawer>
      )}
    </>
  );
}
export function Communications() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0),
    [channel, setChannel] = useState(""),
    [selected, setSelected] = useState("");
  const detail = useResource<Row>(
      selected ? `/communications/${selected}` : "/overview",
      revision,
    ),
    evidence = useResource<Row>(
      selected ? `/communications/${selected}/evidence` : "/overview",
      revision,
    );
  const change = () => setRevision((v) => v + 1);
  return (
    <>
      <div className="filters standalone">
        <label>
          Channel
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="">All channels</option>
            <option>EMAIL</option>
            <option>CHAT</option>
          </select>
        </label>
      </div>
      <PagedTable
        key={channel}
        path={
          channel ? `/communications?channel=${channel}` : "/communications"
        }
        title="Communication archive"
        caption="Preserved synthetic emails and chats with employee identity and trade correlation"
        revision={revision}
        columns={[
          { key: "channel", render: (r) => <Badge value={r.channel} /> },
          { key: "sender" },
          { key: "subject" },
          { key: "employee_id" },
          { key: "timestamp", render: (r) => when(r.timestamp) },
        ]}
        onSelect={(r) => setSelected(r.id)}
      />
      {user?.role !== "VIEWER" && (
        <div className="two-column">
          <Form
            title="Synthetic email and chat feed"
            caption="Includes suspicious phrases and ordinary discussion as a negative control."
            fields={[seedField]}
            submit="Generate communications"
            onSubmit={async (v) => {
              const result = await api(
                `/communications/simulate?seed=${v.seed}`,
                {},
              );
              change();
              return result;
            }}
          />
          <Form
            title="Ingest a communication"
            caption="Source keys are immutable idempotency identifiers."
            fields={[
              {
                name: "source_key",
                label: "Unique source key",
                maxLength: 200,
              },
              { name: "channel", label: "Channel", options: ["EMAIL", "CHAT"] },
              timeField,
              {
                name: "sender",
                label: "Sender email or employee ID",
                maxLength: 254,
              },
              {
                name: "recipients",
                label: "Recipient identities (comma separated)",
              },
              { name: "subject", label: "Subject", optional: true },
              {
                name: "body",
                label: "Message body",
                type: "textarea",
                maxLength: 100000,
              },
            ]}
            submit="Preserve and screen message"
            onSubmit={async (v) => {
              const result = await api("/communications/ingest", {
                messages: [
                  {
                    ...v,
                    recipients: v.recipients
                      .split(",")
                      .map((x) => x.trim())
                      .filter(Boolean),
                  },
                ],
              });
              change();
              return result;
            }}
          />
        </div>
      )}
      {selected && (
        <Drawer title="Communication evidence" onClose={() => setSelected("")}>
          <Notice error={detail.error || evidence.error} />
          {detail.loading ? (
            <p>Loading preserved message…</p>
          ) : (
            detail.data && (
              <>
                <Badge value={detail.data.channel} />
                <h3 className="spaced">
                  {detail.data.subject || "Chat message"}
                </h3>
                <Facts
                  row={detail.data}
                  keys={[
                    "sender",
                    "recipients",
                    "timestamp",
                    "employee_id",
                    "source_key",
                  ]}
                />
                <blockquote className="message-body">
                  {detail.data.body}
                </blockquote>
                <div
                  className={`notice ${evidence.data?.hash_verified ? "success" : "error"}`}
                  role="status"
                >
                  {evidence.loading
                    ? "Verifying preserved source…"
                    : evidence.data?.hash_verified
                      ? "SHA256 integrity verified"
                      : "Source integrity could not be verified"}
                  <div className="hash">{detail.data.content_sha256}</div>
                </div>
                <h3>Surveillance findings</h3>
                {detail.data.signals?.length ? (
                  detail.data.signals.map((s: Row) => (
                    <article className="evidence-block" key={s.id}>
                      <Badge value={s.severity} />
                      <h3>{s.title}</h3>
                      <p>{s.explanation}</p>
                      <Json
                        value={s.evidence}
                        title="Keyword spans, entity matches and correlated event IDs"
                      />
                    </article>
                  ))
                ) : (
                  <p className="muted">
                    No surveillance rules matched this communication.
                  </p>
                )}
                <h3 className="spaced">Correlated executions</h3>
                <Table
                  rows={detail.data.related_events}
                  columns={[
                    { key: "instrument_id" },
                    { key: "side" },
                    { key: "quantity" },
                    { key: "price" },
                    { key: "timestamp", render: (r) => when(r.timestamp) },
                  ]}
                />
                <Json
                  value={{
                    communication: detail.data,
                    verification: evidence.data,
                  }}
                />
              </>
            )
          )}
        </Drawer>
      )}
    </>
  );
}
