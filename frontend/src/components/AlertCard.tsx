import { Alert } from '../types';

interface AlertCardProps {
  alert: Alert;
  onClick: (alert: Alert) => void;
}

const severityColors = {
  LOW: '#3b82f6',
  MEDIUM: '#f59e0b',
  HIGH: '#ef4444',
  CRITICAL: '#dc2626'
};

const statusColors = {
  OPEN: '#10b981',
  INVESTIGATING: '#f59e0b',
  RESOLVED: '#6b7280',
  FALSE_POSITIVE: '#6b7280'
};

export function AlertCard({ alert, onClick }: AlertCardProps) {
  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  return (
    <div
      onClick={() => onClick(alert)}
      style={{
        border: '1px solid #e5e7eb',
        borderRadius: '8px',
        padding: '16px',
        marginBottom: '12px',
        cursor: 'pointer',
        backgroundColor: '#fff',
        transition: 'box-shadow 0.2s',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.boxShadow = '0 4px 6px -1px rgba(0, 0, 0, 0.1)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
        <div>
          <span
            style={{
              display: 'inline-block',
              padding: '4px 8px',
              borderRadius: '4px',
              fontSize: '12px',
              fontWeight: '600',
              color: '#fff',
              backgroundColor: severityColors[alert.severity],
              marginRight: '8px'
            }}
          >
            {alert.severity}
          </span>
          <span
            style={{
              display: 'inline-block',
              padding: '4px 8px',
              borderRadius: '4px',
              fontSize: '12px',
              fontWeight: '600',
              color: '#fff',
              backgroundColor: statusColors[alert.status]
            }}
          >
            {alert.status}
          </span>
        </div>
        <div style={{ fontSize: '12px', color: '#6b7280' }}>
          {formatDate(alert.created_at)}
        </div>
      </div>

      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontSize: '14px', fontWeight: '600', color: '#1f2937', marginBottom: '4px' }}>
          {alert.alert_type.replace('_', ' ')}
        </div>
        <div style={{ fontSize: '13px', color: '#4b5563' }}>
          {alert.instrument_id}
        </div>
      </div>

      <div style={{ fontSize: '14px', color: '#374151', marginBottom: '8px' }}>
        {alert.description}
      </div>

      {alert.confidence_score !== undefined && (
        <div style={{ fontSize: '12px', color: '#6b7280' }}>
          Confidence: {(alert.confidence_score * 100).toFixed(1)}%
        </div>
      )}
    </div>
  );
}
