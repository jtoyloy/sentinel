export interface TradingEvent {
  event_id: string;
  event_type: 'ORDER' | 'EXECUTION' | 'CANCEL' | 'MODIFY';
  timestamp: string;
  instrument_id: string;
  trader_id: string;
  account_id: string;
  firm_id: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  order_id: string;
  parent_order_id?: string;
  metadata: Record<string, any>;
  created_at: string;
}

export interface Alert {
  alert_id: string;
  alert_type: 'WASH_TRADE' | 'SPOOFING' | 'LAYERING' | 'MARKING_CLOSE';
  severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  status: 'OPEN' | 'INVESTIGATING' | 'RESOLVED' | 'FALSE_POSITIVE';
  created_at: string;
  instrument_id: string;
  time_window_start: string;
  time_window_end: string;
  description: string;
  explanation: string;
  evidence: {
    event_ids: string[];
    [key: string]: any;
  };
  confidence_score?: number;
  assigned_to?: string;
  resolved_at?: string;
  resolution_notes?: string;
  metadata: Record<string, any>;
}

export interface WebSocketMessage {
  type: string;
  alert_id?: string;
  alert_type?: string;
  severity?: string;
  instrument_id?: string;
  description?: string;
  confidence_score?: number;
}
