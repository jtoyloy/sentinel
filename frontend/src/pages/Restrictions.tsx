import { useState } from "react";
import { useAuth } from "../auth";
import { api, Row, when } from "../platformApi";
import { Badge, Drawer, Facts, Form, Json, PagedTable, Panel } from "../ui";
const employee = { name: "employee_id", label: "Employee ID", maxLength: 50 };
const instrument = {
  name: "instrument_id",
  label: "Instrument (uppercase symbol)",
  maxLength: 50,
};
export default function Restrictions() {
  const { user } = useAuth(),
    [revision, setRevision] = useState(0),
    [detail, setDetail] = useState<Row | null>(null),
    [approval, setApproval] = useState<Row | null>(null);
  const change = () => setRevision((v) => v + 1);
  return (
    <>
      <Panel
        title="Firmwide policy controls"
        caption="Versioned policies, reproducible pre-trade decisions and independent exemption approval"
      >
        <p className="panel-copy">
          Checks preserve the policy version and employee attributes used for
          each decision. Historical ingestion remains available for
          surveillance.
        </p>
      </Panel>
      <PagedTable
        path="/restrictions/policies"
        title="Restricted securities and policy versions"
        revision={revision}
        columns={[
          { key: "name" },
          { key: "version" },
          { key: "instrument_id" },
          { key: "action", render: (r) => <Badge value={r.action} /> },
          { key: "status", render: (r) => <Badge value={r.status} /> },
          { key: "department" },
          { key: "effective_from", render: (r) => when(r.effective_from) },
        ]}
        onSelect={setDetail}
      />
      {user?.role === "ADMIN" && (
        <Form
          title="Publish a policy version"
          caption="Reusing a policy name retires its previous active version atomically."
          fields={[
            { name: "name", label: "Policy name", maxLength: 100 },
            instrument,
            { name: "action", label: "Decision", options: ["BLOCK", "REVIEW"] },
            {
              name: "reason",
              label: "Policy rationale",
              type: "textarea",
              maxLength: 4000,
            },
            {
              name: "effective_from",
              label: "Effective from (ISO with timezone)",
              value: new Date().toISOString(),
            },
            {
              name: "effective_to",
              label: "Effective to (ISO with timezone)",
              optional: true,
            },
            {
              name: "department",
              label: "Department scope (optional)",
              optional: true,
              maxLength: 100,
            },
          ]}
          submit="Publish policy"
          onSubmit={async (v) => {
            const payload: Row = { ...v };
            if (!v.effective_to) delete payload.effective_to;
            if (!v.department) delete payload.department;
            const result = await api("/restrictions/policies", payload);
            change();
            return result;
          }}
        />
      )}
      {user?.role !== "VIEWER" && (
        <div className="two-column">
          <Form
            title="Pre-trade permission check"
            caption="Each ALLOW / BLOCK / REVIEW result is persisted with its full policy snapshot."
            fields={[
              employee,
              instrument,
              { name: "side", label: "Side", options: ["BUY", "SELL"] },
              {
                name: "quantity",
                label: "Quantity",
                type: "number",
                value: "100",
                min: 1,
                max: 2147483647,
              },
              {
                name: "price",
                label: "Price",
                type: "number",
                value: "100.00",
                step: "0.00000001",
                min: 0.00000001,
              },
            ]}
            submit="Check permission"
            onSubmit={async (v) => {
              const result = await api("/restrictions/check", {
                ...v,
                quantity: Number(v.quantity),
              });
              change();
              setDetail(result);
              return result;
            }}
          />
          <Form
            title="Request an exemption"
            caption="A different administrator must approve. Expired exemptions cannot authorize trading."
            fields={[
              employee,
              instrument,
              {
                name: "reason",
                label: "Business justification",
                type: "textarea",
                maxLength: 4000,
              },
              { name: "expires_at", label: "Expiry (ISO with timezone)" },
            ]}
            submit="Request approval"
            onSubmit={async (v) => {
              const result = await api("/restrictions/exemptions", v);
              change();
              return result;
            }}
          />
        </div>
      )}
      <PagedTable
        path="/restrictions/decisions"
        title="Pre-trade decision ledger"
        revision={revision}
        columns={[
          { key: "employee_id" },
          { key: "instrument_id" },
          { key: "decision", render: (r) => <Badge value={r.decision} /> },
          { key: "requester" },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={setDetail}
      />
      <PagedTable
        path="/restrictions/exemptions"
        title="Exemption approvals"
        caption="Self-approval is prohibited and enforced by the server."
        revision={revision}
        columns={[
          { key: "employee_id" },
          { key: "instrument_id" },
          { key: "status", render: (r) => <Badge value={r.status} /> },
          { key: "requested_by" },
          { key: "expires_at", render: (r) => when(r.expires_at) },
          {
            key: "approval",
            render: (r) =>
              user?.role === "ADMIN" &&
              r.status === "PENDING" &&
              r.requested_by !== user.username ? (
                <button className="button small" onClick={() => setApproval(r)}>
                  Review approval
                </button>
              ) : r.requested_by === user?.username &&
                r.status === "PENDING" ? (
                "Independent approver required"
              ) : (
                r.approved_by || "—"
              ),
          },
        ]}
        onSelect={setDetail}
      />
      <PagedTable
        path="/restrictions/audit"
        title="Append-only policy audit"
        revision={revision}
        columns={[
          { key: "action" },
          { key: "actor" },
          { key: "target_type" },
          { key: "created_at", render: (r) => when(r.created_at) },
        ]}
        onSelect={setDetail}
      />
      {detail && (
        <Drawer
          title="Policy evidence and decision"
          onClose={() => setDetail(null)}
        >
          {detail.decision && (
            <div className="decision-result">
              <Badge value={detail.decision} />
              <h3>
                {detail.instrument_id} · {detail.employee_id}
              </h3>
              {detail.reasons?.map((r: string) => (
                <p key={r}>{r}</p>
              ))}
            </div>
          )}
          <Facts
            row={detail}
            keys={Object.keys(detail).filter(
              (k) => typeof detail[k] !== "object",
            )}
          />
          <Json
            value={detail}
            title="Exact policy and employee snapshots / audit details"
          />
        </Drawer>
      )}
      {approval && (
        <Drawer
          title="Independent exemption approval"
          onClose={() => setApproval(null)}
        >
          <Facts
            row={approval}
            keys={[
              "employee_id",
              "instrument_id",
              "reason",
              "requested_by",
              "expires_at",
            ]}
          />
          <Form
            title="Record approval"
            fields={[
              {
                name: "notes",
                label: "Approval rationale",
                type: "textarea",
                maxLength: 4000,
              },
            ]}
            submit="Approve exemption"
            onSubmit={async (v) => {
              const result = await api(
                `/restrictions/exemptions/${approval.id}/approve`,
                v,
              );
              change();
              setApproval(null);
              return result;
            }}
          />
        </Drawer>
      )}
    </>
  );
}
