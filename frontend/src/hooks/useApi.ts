import { Alert, TradingEvent } from '../types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body); } catch { /* HTTP status is the fallback */ }
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}
export function fetchAlerts(params: { status?: string; alert_type?: string; instrument_id?: string; limit?: number; offset?: number } = {}): Promise<Alert[]> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== '') query.set(key, String(value)); });
  return request(`/alerts?${query}`);
}
export const fetchAlertById = (id: string) => request<Alert>(`/alerts/${encodeURIComponent(id)}`);
export const fetchEvidence = (id: string) => request<TradingEvent[]>(`/alerts/${encodeURIComponent(id)}/evidence`);
export interface AuditEntry { id?: string; audit_id?: string; previous_status: string; new_status: string; actor: string; notes: string; created_at: string; }
export const fetchHistory = (id: string) => request<AuditEntry[]>(`/alerts/${encodeURIComponent(id)}/history`);
export const updateAlert = (id: string, status: string, actor: string, notes: string) => request<Alert>(`/alerts/${encodeURIComponent(id)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status, actor, notes})});
export interface Stats { events: number; alerts: number; open_alerts: number; critical_alerts: number; }
export const fetchStats = () => request<Stats>('/stats');
export interface ReplayResult { scenario: string; seed: number; base_time: string; run_id: string; events_generated: number; message: string; }
export function replayScenario(scenario: string, seed: number, baseTime: string) {
  return request<ReplayResult>(`/replay/${scenario}?${new URLSearchParams({seed: String(seed), base_time: baseTime})}`, {method: 'POST'});
}
