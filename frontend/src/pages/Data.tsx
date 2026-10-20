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
export default function Data() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0),
    [selected, setSelected] = useState(""),
    [raw, setRaw] = useState<Row | null>(null);
  const detail = useResource<Row>(
    selected ? `/data/reconciliations/${selected}` : "/overview",
    revision,
  );
  const change = () => setRevision((v) => v + 1);
  return (
    <>
      {user?.role !== "VIEWER" && (
        <div className="two-column">
          <Form
            title="Simulated broker feeds"
            caption="ALPHA and BETA use different source schemas. Same seed permits reconciliation."
            fields={[
              {
                name: "source",
                label: "Broker source",
                options: ["ALPHA", "BETA"],
              },
              {
                name: "seed",
                label: "Seed",
                type: "number",
                value: "42",
                min: 0,
                max: 2147483647,
              },
            ]}
            submit="Normalize and ingest"
            onSubmit={async (v) => {
              const result = await api(
                `/data/feeds/${v.source}/simulate?seed=${v.seed}`,
                {},
              );
              change();
              return result;
            }}
          />
          <Form
            title="Reconcile broker executions"
            caption="Compare persisted broker references for missing, duplicate and mismatched executions."
            fields={[]}
            submit="Run reconciliation"
            onSubmit={async () => {
              const result = await api("/data/reconcile", {});
              change();
              setSelected(result.id);
              return result;
            }}
          />
        </div>
      )}
      <PagedTable
        path="/data/batches"
        title="Broker ingestion batches"
        revision={revision}
        columns={[
          { key: "source" },
          { key: "received" },
          { key: "normalized" },
          { key: "rejected" },
          { key: "duplicates" },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={setRaw}
      />
      <PagedTable
        path="/data/reconciliations"
        title="Reconciliation runs"
        revision={revision}
        columns={[
          { key: "status", render: (r) => <Badge value={r.status} /> },
          { key: "compared" },
          { key: "matched" },
          { key: "missing" },
          { key: "duplicates" },
          { key: "mismatched" },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={(r) => setSelected(r.id)}
      />
      <PagedTable
        path="/data/quality"
        title="Normalization and source quality issues"
        revision={revision}
        columns={[
          { key: "source" },
          { key: "source_event_key" },
          { key: "reason" },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={setRaw}
      />
      <PagedTable
        path="/data/securities"
        title="Security master"
        caption="Canonical instrument reference data enriches newly ingested events."
        revision={revision}
        columns={[
          { key: "instrument_id" },
          { key: "name" },
          { key: "isin" },
          { key: "exchange" },
          { key: "currency" },
          { key: "asset_class" },
        ]}
        onSelect={setRaw}
      />
      {user?.role === "ADMIN" && (
        <Form
          title="Add security reference"
          fields={[
            {
              name: "instrument_id",
              label: "Instrument symbol",
              maxLength: 50,
            },
            { name: "name", label: "Security name", maxLength: 200 },
            { name: "isin", label: "ISIN", maxLength: 12 },
            { name: "exchange", label: "Exchange", maxLength: 50 },
            { name: "currency", label: "Currency", value: "USD", maxLength: 3 },
            {
              name: "asset_class",
              label: "Asset class",
              value: "EQUITY",
              maxLength: 50,
            },
          ]}
          submit="Add security"
          onSubmit={async (v) => {
            const result = await api("/data/securities", v);
            change();
            return result;
          }}
        />
      )}
      {user?.role !== "VIEWER" && (
        <Form
          title="Import broker source records"
          caption="Broker-specific JSON payloads are preserved with hashes and normalization results."
          fields={[
            { name: "source", label: "Broker", options: ["ALPHA", "BETA"] },
            {
              name: "records",
              label: "Source record array (JSON)",
              type: "textarea",
              value: "[]",
              maxLength: 500000,
            },
          ]}
          submit="Ingest broker records"
          onSubmit={async (v) => {
            const result = await api(`/data/feeds/${v.source}/ingest`, {
              records: JSON.parse(v.records),
            });
            change();
            return result;
          }}
        />
      )}
      {selected && (
        <Drawer title="Reconciliation evidence" onClose={() => setSelected("")}>
          <Notice error={detail.error} />
          {detail.loading ? (
            <p>Loading reconciliation…</p>
          ) : (
            detail.data && (
              <>
                <Facts
                  row={detail.data}
                  keys={[
                    "id",
                    "status",
                    "compared",
                    "matched",
                    "missing",
                    "duplicates",
                    "mismatched",
                    "created_at",
                  ]}
                />
                <Table
                  rows={detail.data.issues}
                  columns={[
                    { key: "kind", render: (r) => <Badge value={r.kind} /> },
                    { key: "execution_reference" },
                    { key: "explanation" },
                  ]}
                  onSelect={setRaw}
                />
                {detail.data.issues?.map((i: Row) => (
                  <article className="evidence-block" key={i.id}>
                    <h3>
                      {i.kind} · {i.execution_reference}
                    </h3>
                    <p>{i.explanation}</p>
                    <Json value={i.evidence} />
                  </article>
                ))}
              </>
            )
          )}
        </Drawer>
      )}
      {raw && (
        <Drawer title="Preserved data record" onClose={() => setRaw(null)}>
          <Facts
            row={raw}
            keys={Object.keys(raw).filter((k) => typeof raw[k] !== "object")}
          />
          <Json value={raw} />
        </Drawer>
      )}
    </>
  );
}
export function Reports() {
  const { user } = useAuth();
  const [revision, setRevision] = useState(0);
  return (
    <>
      {user?.role !== "VIEWER" && (
        <Form
          title="Regulatory-style execution export"
          caption="Synthetic canonical executions only. Every CSV is audited with requester, row count and SHA256. No regulator-specific certification is implied."
          fields={[
            {
              name: "from_time",
              label: "From (ISO timestamp with timezone)",
              optional: true,
            },
            {
              name: "to_time",
              label: "To (ISO timestamp with timezone)",
              optional: true,
            },
          ]}
          submit="Download audited CSV"
          onSubmit={async (v) => {
            const params = new URLSearchParams();
            for (const [k, value] of Object.entries(v))
              if (value) params.set(k, new Date(value).toISOString());
            const response = await fetch(`/api/reports/trades.csv?${params}`, {
              credentials: "same-origin",
            });
            if (!response.ok) {
              const body = await response.json();
              throw new Error(
                `${response.status}: ${JSON.stringify(body.detail)}`,
              );
            }
            const blob = await response.blob(),
              url = URL.createObjectURL(blob),
              a = document.createElement("a");
            a.href = url;
            a.download = "sentinel-synthetic-trades.csv";
            document.body.append(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            setRevision((v) => v + 1);
            return { downloaded_bytes: blob.size };
          }}
        />
      )}
      <PagedTable
        path="/reports/exports"
        title="Report export audit"
        revision={revision}
        columns={[
          { key: "format" },
          { key: "version" },
          { key: "row_count", title: "Execution rows" },
          { key: "requester" },
          { key: "created_at", render: (r) => when(r.created_at) },
          {
            key: "sha256",
            render: (r) => (
              <code className="hash" title={r.sha256}>
                {r.sha256}
              </code>
            ),
          },
        ]}
        onSelect={(r) =>
          window.dispatchEvent(
            new CustomEvent("sentinel-inspect", { detail: r }),
          )
        }
      />
    </>
  );
}
