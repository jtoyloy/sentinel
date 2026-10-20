import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { api, Row, text, pretty } from "./platformApi";
export function useResource<T = Row[]>(path: string, revision = 0) {
  const [state, setState] = useState<{
    path: string;
    data: T | null;
    error: string;
    loading: boolean;
  }>({ path, data: null, error: "", loading: true });
  const [local, setLocal] = useState(0);
  const refresh = useCallback(() => setLocal((n) => n + 1), []);
  useEffect(() => {
    let cancelled = false;
    setState((old) => ({
      path,
      data: old.path === path ? old.data : null,
      error: "",
      loading: true,
    }));
    api<T>(path)
      .then((data) => {
        if (!cancelled) setState({ path, data, error: "", loading: false });
      })
      .catch((e) => {
        if (!cancelled)
          setState((old) => ({
            ...old,
            path,
            error: e.message,
            loading: false,
          }));
      });
    return () => {
      cancelled = true;
    };
  }, [path, revision, local]);
  return {
    ...(state.path === path ? state : { data: null, error: "", loading: true }),
    refresh,
  };
}
export function Panel({
  title,
  caption,
  children,
  actions,
}: {
  title: string;
  caption?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          {caption && <p>{caption}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}
export function Notice({ error }: { error: string }) {
  return error ? (
    <div className="notice error" role="alert">
      {error}
    </div>
  ) : null;
}
export function Badge({ value }: { value: unknown }) {
  const v = text(value);
  return (
    <span
      className={`badge badge-${v.toLowerCase()} severity-${v.toLowerCase()} status-${v.toLowerCase()}`}
    >
      {pretty(v)}
    </span>
  );
}
export type Column = {
  key: string;
  title?: string;
  render?: (row: Row) => ReactNode;
};
export function Table({
  rows,
  columns,
  onSelect,
  loading = false,
  empty = "No persisted records yet.",
}: {
  rows?: Row[] | null;
  columns: Column[];
  onSelect?: (row: Row) => void;
  loading?: boolean;
  empty?: string;
}) {
  return (
    <>
      <div className="table-scroll">
        <table className="platform-table">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key}>{c.title || pretty(c.key)}</th>
              ))}
              {onSelect && <th>Evidence</th>}
            </tr>
          </thead>
          <tbody>
            {rows?.map((row, i) => (
              <tr key={row.id || row.employee_id || row.instrument_id || i}>
                {columns.map((c) => (
                  <td key={c.key}>
                    {c.render ? c.render(row) : text(row[c.key])}
                  </td>
                ))}
                {onSelect && (
                  <td>
                    <button
                      className="button small"
                      onClick={() => onSelect(row)}
                    >
                      Inspect ↗
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows?.length && (
        <div className="empty">
          <h3>{loading ? "Loading records…" : empty}</h3>
          <p>
            {loading
              ? "Reading authoritative data from Sentinel."
              : "Run a synthetic feed or adjust the filters to populate this view."}
          </p>
        </div>
      )}
    </>
  );
}
export function Json({
  value,
  title = "Preserved source and complete evidence",
}: {
  value: unknown;
  title?: string;
}) {
  return (
    <details className="raw-evidence">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function ResourceTable({
  path,
  columns,
  revision = 0,
  onSelect,
  title,
  caption,
}: {
  path: string;
  columns: Column[];
  revision?: number;
  onSelect?: (row: Row) => void;
  title: string;
  caption?: string;
}) {
  const r = useResource(path, revision);
  return (
    <Panel
      title={title}
      caption={caption}
      actions={
        <button
          className="button small"
          onClick={r.refresh}
          disabled={r.loading}
        >
          Refresh
        </button>
      }
    >
      <Notice error={r.error} />
      <Table
        rows={r.data}
        columns={columns}
        onSelect={onSelect}
        loading={r.loading}
      />
    </Panel>
  );
}
export interface Field {
  name: string;
  label: string;
  type?: string;
  options?: string[];
  optional?: boolean;
  value?: string;
  hint?: string;
  min?: number;
  max?: number;
  step?: string;
  maxLength?: number;
}
export function Form({
  title,
  caption,
  fields,
  submit,
  onSubmit,
  children,
}: {
  title: string;
  caption?: string;
  fields: Field[];
  submit: string;
  onSubmit: (data: Record<string, string>) => Promise<unknown>;
  children?: ReactNode;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [result, setResult] = useState<unknown>(null);
  async function send(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries()) as Record<
      string,
      string
    >;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult((await onSubmit(values)) ?? { saved: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel title={title} caption={caption}>
      <form className="platform-form" onSubmit={send}>
        <div className="fields">
          {fields.map((f) => (
            <label key={f.name}>
              {f.label}
              {f.options ? (
                <select
                  aria-label={f.label}
                  name={f.name}
                  defaultValue={f.value || f.options[0]}
                >
                  {f.options.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              ) : f.type === "textarea" ? (
                <textarea
                  aria-label={f.label}
                  name={f.name}
                  required={!f.optional}
                  defaultValue={f.value}
                  rows={4}
                  maxLength={f.maxLength || 10000}
                />
              ) : (
                <input
                  aria-label={f.label}
                  name={f.name}
                  required={!f.optional}
                  type={f.type || "text"}
                  defaultValue={f.value}
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  maxLength={f.maxLength || 500}
                />
              )}
              {f.hint && <small>{f.hint}</small>}
            </label>
          ))}
        </div>
        {children}
        <button className="button primary" disabled={busy}>
          {busy ? "Saving…" : submit}
        </button>
      </form>
      <Notice error={error} />
      {result !== null && (
        <div className="notice success" role="status">
          <strong>Request completed successfully.</strong>
          <Json value={result} title="View result" />
        </div>
      )}
    </Panel>
  );
}
export function Drawer({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const targets = dialog.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input,select,textarea,summary,a[href]",
        );
        if (!targets?.length) return;
        const first = targets[0],
          last = targets[targets.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = overflow;
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="drawer-header">
          <h2>{title}</h2>
          <button
            autoFocus
            className="icon-button"
            aria-label="Close detail"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="drawer-content">{children}</div>
      </section>
    </div>
  );
}
export function Facts({ row, keys }: { row: Row; keys: string[] }) {
  return (
    <dl className="facts">
      {keys.map((k) => (
        <div key={k}>
          <dt>{pretty(k)}</dt>
          <dd>{text(row[k])}</dd>
        </div>
      ))}
    </dl>
  );
}
export function PagedTable({
  path,
  title,
  columns,
  revision,
  onSelect,
  caption,
}: {
  path: string;
  title: string;
  columns: Column[];
  revision: number;
  onSelect?: (row: Row) => void;
  caption?: string;
}) {
  const [offset, setOffset] = useState(0);
  const r = useResource<Row[]>(
    `${path}${path.includes("?") ? "&" : "?"}limit=26&offset=${offset}`,
    revision,
  );
  return (
    <Panel
      title={title}
      caption={caption}
      actions={
        <button
          className="button small"
          onClick={r.refresh}
          disabled={r.loading}
        >
          Refresh
        </button>
      }
    >
      <Notice error={r.error} />
      <Table
        rows={r.data?.slice(0, 25)}
        columns={columns}
        onSelect={onSelect}
        loading={r.loading}
      />
      <div className="pagination">
        <span>
          {r.data?.length ? `Showing ${offset + 1}–${offset + r.data.slice(0, 25).length}` : r.loading ? 'Loading records…' : 'No records'}
        </span>
        <div>
          <button
            className="button small"
            disabled={offset === 0 || r.loading}
            onClick={() => setOffset((v) => Math.max(0, v - 25))}
          >
            ← Previous
          </button>
          <button
            className="button small"
            disabled={!r.data || r.data.length < 26 || r.loading}
            onClick={() => setOffset((v) => v + 25)}
          >
            Next →
          </button>
        </div>
      </div>
    </Panel>
  );
}
