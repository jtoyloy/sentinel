"""Add institutional compliance domains, authenticated access and operational state.

Frozen additive PostgreSQL DDL; preserves all 0001 source events and alerts.
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL_STATEMENTS = (
    'ALTER TABLE trading_events ADD COLUMN source_metadata JSONB',
    'CREATE TABLE compliance_signals (\n\tid UUID NOT NULL,\n\tdomain VARCHAR(30) NOT NULL,\n\trule VARCHAR(80) NOT NULL,\n\tfingerprint VARCHAR(64) NOT NULL,\n\tseverity VARCHAR(20) NOT NULL,\n\tstatus VARCHAR(20) NOT NULL,\n\ttitle VARCHAR(300) NOT NULL,\n\texplanation TEXT NOT NULL,\n\tevidence JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tUNIQUE (fingerprint)\n)',
    'CREATE INDEX ix_compliance_signals_domain ON compliance_signals (domain)',
    'CREATE INDEX ix_compliance_signals_status ON compliance_signals (status)',
    'CREATE TABLE employee_feed_issues (\n\tid UUID NOT NULL,\n\tsource VARCHAR(50) NOT NULL,\n\tsource_key VARCHAR(200),\n\treason VARCHAR(2000) NOT NULL,\n\tpayload JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    "CREATE TABLE employees (\n\temployee_id VARCHAR(50) NOT NULL,\n\tfull_name VARCHAR(200) NOT NULL,\n\temail VARCHAR(254) NOT NULL,\n\tdepartment VARCHAR(100) NOT NULL,\n\trole VARCHAR(100) NOT NULL,\n\tstatus VARCHAR(20) NOT NULL,\n\tmanager_id VARCHAR(50),\n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (employee_id),\n\tCONSTRAINT employee_status CHECK (status IN ('ACTIVE', 'INACTIVE')),\n\tUNIQUE (email)\n)",
    'CREATE TABLE feed_batches (\n\tid UUID NOT NULL,\n\tsource VARCHAR(10) NOT NULL,\n\treceived INTEGER NOT NULL,\n\tnormalized INTEGER NOT NULL,\n\trejected INTEGER NOT NULL,\n\tduplicates INTEGER NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE TABLE investigations (\n\tid UUID NOT NULL,\n\ttitle VARCHAR(200) NOT NULL,\n\tpriority VARCHAR(20) NOT NULL,\n\towner VARCHAR(100) NOT NULL,\n\tsummary TEXT NOT NULL,\n\tstatus VARCHAR(20) NOT NULL,\n\tsignal_ids JSONB NOT NULL,\n\talert_ids JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE TABLE login_attempts (\n\tid UUID NOT NULL,\n\tidentity_hash VARCHAR(64) NOT NULL,\n\tremote_hash VARCHAR(64) NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_login_attempts_created_at ON login_attempts (created_at)',
    'CREATE INDEX ix_login_attempts_identity_hash ON login_attempts (identity_hash)',
    'CREATE INDEX ix_login_attempts_remote_hash ON login_attempts (remote_hash)',
    'CREATE TABLE platform_audit (\n\tid UUID NOT NULL,\n\ttarget_type VARCHAR(40) NOT NULL,\n\ttarget_id VARCHAR(100) NOT NULL,\n\taction VARCHAR(40) NOT NULL,\n\tactor VARCHAR(100) NOT NULL,\n\tnotes TEXT NOT NULL,\n\tdetails JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_platform_audit_target_id ON platform_audit (target_id)',
    'CREATE INDEX ix_platform_audit_target_type ON platform_audit (target_type)',
    'CREATE TABLE reconciliation_runs (\n\tid UUID NOT NULL,\n\tstatus VARCHAR(20) NOT NULL,\n\tcompared INTEGER NOT NULL,\n\tmatched INTEGER NOT NULL,\n\tmissing INTEGER NOT NULL,\n\tduplicates INTEGER NOT NULL,\n\tmismatched INTEGER NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE TABLE report_exports (\n\tid UUID NOT NULL,\n\tformat VARCHAR(50) NOT NULL,\n\tversion VARCHAR(20) NOT NULL,\n\trow_count INTEGER NOT NULL,\n\tsha256 VARCHAR(64) NOT NULL,\n\trequester VARCHAR(100) NOT NULL,\n\tfilters JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE TABLE restriction_audit (\n\tid UUID NOT NULL,\n\ttarget_type VARCHAR(20) NOT NULL,\n\ttarget_id UUID NOT NULL,\n\taction VARCHAR(40) NOT NULL,\n\tactor VARCHAR(100) NOT NULL,\n\tdetails JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_restriction_audit_target_id ON restriction_audit (target_id)',
    "CREATE TABLE restriction_policies (\n\tid UUID NOT NULL,\n\tname VARCHAR(100) NOT NULL,\n\tversion INTEGER NOT NULL,\n\tstatus VARCHAR(20) NOT NULL,\n\tinstrument_id VARCHAR(50) NOT NULL,\n\taction VARCHAR(10) NOT NULL,\n\treason TEXT NOT NULL,\n\teffective_from TIMESTAMP WITH TIME ZONE NOT NULL,\n\teffective_to TIMESTAMP WITH TIME ZONE,\n\tdepartment VARCHAR(100),\n\tcreated_by VARCHAR(100) NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tCONSTRAINT uq_restriction_policy_version UNIQUE (name, version),\n\tCONSTRAINT restriction_policy_action CHECK (action IN ('BLOCK', 'REVIEW'))\n)",
    'CREATE INDEX ix_restriction_policies_instrument_id ON restriction_policies (instrument_id)',
    'CREATE INDEX ix_restriction_policies_name ON restriction_policies (name)',
    'CREATE TABLE securities (\n\tinstrument_id VARCHAR(50) NOT NULL,\n\tname VARCHAR(200) NOT NULL,\n\tisin VARCHAR(12) NOT NULL,\n\texchange VARCHAR(50) NOT NULL,\n\tcurrency VARCHAR(3) NOT NULL,\n\tasset_class VARCHAR(50) NOT NULL,\n\tPRIMARY KEY (instrument_id),\n\tUNIQUE (isin)\n)',
    'CREATE TABLE stream_failures (\n\tid UUID NOT NULL,\n\tstream_id VARCHAR(50) NOT NULL,\n\tpayload JSONB NOT NULL,\n\terror VARCHAR(300) NOT NULL,\n\tattempts INTEGER NOT NULL,\n\tstatus VARCHAR(30) NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tUNIQUE (stream_id)\n)',
    "CREATE TABLE users (\n\tid UUID NOT NULL,\n\tusername VARCHAR(100) NOT NULL,\n\tpassword_hash VARCHAR(256) NOT NULL,\n\trole VARCHAR(20) NOT NULL,\n\tactive BOOLEAN NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tCONSTRAINT check_user_role CHECK (role IN ('VIEWER','ANALYST','ADMIN')),\n\tUNIQUE (username)\n)",
    'CREATE TABLE worker_heartbeat (\n\tname VARCHAR(50) NOT NULL,\n\tlast_success TIMESTAMP WITH TIME ZONE,\n\tlast_failure TIMESTAMP WITH TIME ZONE,\n\tprocessed INTEGER NOT NULL,\n\tfailures INTEGER NOT NULL,\n\tlast_error VARCHAR(300),\n\tPRIMARY KEY (name)\n)',
    'CREATE TABLE auth_sessions (\n\tid UUID NOT NULL,\n\tuser_id UUID NOT NULL,\n\ttoken_hash VARCHAR(64) NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tFOREIGN KEY(user_id) REFERENCES users (id),\n\tUNIQUE (token_hash)\n)',
    'CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at)',
    'CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)',
    'CREATE TABLE broker_records (\n\tid UUID NOT NULL,\n\tbatch_id UUID NOT NULL,\n\tsource VARCHAR(10) NOT NULL,\n\tsource_event_key VARCHAR(100) NOT NULL,\n\texecution_reference VARCHAR(100),\n\traw_payload JSONB NOT NULL,\n\tcontent_sha256 VARCHAR(64) NOT NULL,\n\tnormalized_event_id UUID,\n\tstatus VARCHAR(20) NOT NULL,\n\terror TEXT,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tCONSTRAINT uq_broker_source_key UNIQUE (source, source_event_key),\n\tFOREIGN KEY(batch_id) REFERENCES feed_batches (id),\n\tFOREIGN KEY(normalized_event_id) REFERENCES trading_events (event_id)\n)',
    'CREATE INDEX ix_broker_records_execution_reference ON broker_records (execution_reference)',
    'CREATE TABLE communications (\n\tid UUID NOT NULL,\n\tsource_key VARCHAR(200) NOT NULL,\n\tchannel VARCHAR(10) NOT NULL,\n\ttimestamp TIMESTAMP WITH TIME ZONE NOT NULL,\n\tsender VARCHAR(254) NOT NULL,\n\trecipients JSONB NOT NULL,\n\tsubject VARCHAR(500) NOT NULL,\n\tbody TEXT NOT NULL,\n\temployee_id VARCHAR(50),\n\tcontent_sha256 VARCHAR(64) NOT NULL,\n\traw_payload JSONB NOT NULL,\n\tPRIMARY KEY (id),\n\tUNIQUE (source_key),\n\tFOREIGN KEY(employee_id) REFERENCES employees (employee_id)\n)',
    'CREATE INDEX ix_communications_employee_id ON communications (employee_id)',
    'CREATE INDEX ix_communications_timestamp ON communications (timestamp)',
    'CREATE TABLE data_quality_issues (\n\tid UUID NOT NULL,\n\tbatch_id UUID NOT NULL,\n\tsource VARCHAR(10) NOT NULL,\n\tsource_event_key VARCHAR(100),\n\treason TEXT NOT NULL,\n\tpayload JSONB NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tFOREIGN KEY(batch_id) REFERENCES feed_batches (id)\n)',
    'CREATE TABLE employee_accounts (\n\tid UUID NOT NULL,\n\temployee_id VARCHAR(50) NOT NULL,\n\tfirm_id VARCHAR(50) NOT NULL,\n\taccount_id VARCHAR(50) NOT NULL,\n\tPRIMARY KEY (id),\n\tCONSTRAINT employee_firm_account UNIQUE (firm_id, account_id),\n\tFOREIGN KEY(employee_id) REFERENCES employees (employee_id)\n)',
    'CREATE INDEX ix_employee_accounts_employee_id ON employee_accounts (employee_id)',
    'CREATE TABLE employee_changes (\n\tid UUID NOT NULL,\n\temployee_id VARCHAR(50) NOT NULL,\n\teffective_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tpayload JSONB NOT NULL,\n\tsource_key VARCHAR(200) NOT NULL,\n\tPRIMARY KEY (id),\n\tFOREIGN KEY(employee_id) REFERENCES employees (employee_id),\n\tUNIQUE (source_key)\n)',
    'CREATE INDEX ix_employee_changes_employee_id ON employee_changes (employee_id)',
    'CREATE TABLE reconciliation_issues (\n\tid UUID NOT NULL,\n\trun_id UUID NOT NULL,\n\texecution_reference VARCHAR(100) NOT NULL,\n\tkind VARCHAR(20) NOT NULL,\n\texplanation TEXT NOT NULL,\n\tevidence JSONB NOT NULL,\n\tPRIMARY KEY (id),\n\tFOREIGN KEY(run_id) REFERENCES reconciliation_runs (id)\n)',
    'CREATE TABLE restriction_exemptions (\n\tid UUID NOT NULL,\n\temployee_id VARCHAR(50) NOT NULL,\n\tinstrument_id VARCHAR(50) NOT NULL,\n\treason TEXT NOT NULL,\n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tstatus VARCHAR(20) NOT NULL,\n\trequested_by VARCHAR(100) NOT NULL,\n\trequested_by_id VARCHAR(100) NOT NULL,\n\tapproved_by VARCHAR(100),\n\tapproved_at TIMESTAMP WITH TIME ZONE,\n\tapproval_notes TEXT,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tFOREIGN KEY(employee_id) REFERENCES employees (employee_id)\n)',
    'CREATE INDEX ix_restriction_exemptions_employee_id ON restriction_exemptions (employee_id)',
    "CREATE TABLE restriction_decisions (\n\tid UUID NOT NULL,\n\temployee_id VARCHAR(50) NOT NULL,\n\tinstrument_id VARCHAR(50) NOT NULL,\n\tside VARCHAR(10) NOT NULL,\n\tquantity INTEGER NOT NULL,\n\tprice NUMERIC(20, 8) NOT NULL,\n\tdecision VARCHAR(10) NOT NULL,\n\treasons JSONB NOT NULL,\n\tpolicy_ids JSONB NOT NULL,\n\tpolicy_versions JSONB NOT NULL,\n\tpolicy_snapshot JSONB NOT NULL,\n\temployee_snapshot JSONB NOT NULL,\n\texemption_id UUID,\n\trequester VARCHAR(100) NOT NULL,\n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL,\n\tPRIMARY KEY (id),\n\tCONSTRAINT restriction_decision_outcome CHECK (decision IN ('ALLOW', 'BLOCK', 'REVIEW')),\n\tFOREIGN KEY(employee_id) REFERENCES employees (employee_id),\n\tFOREIGN KEY(exemption_id) REFERENCES restriction_exemptions (id)\n)",
    'CREATE INDEX ix_restriction_decisions_employee_id ON restriction_decisions (employee_id)',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON employee_changes FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON employee_feed_issues FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON communications FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON broker_records FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON data_quality_issues FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON reconciliation_issues FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON report_exports FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON restriction_decisions FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON restriction_audit FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON platform_audit FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_signal_evidence BEFORE UPDATE OF domain,rule,fingerprint,severity,title,explanation,evidence,created_at ON compliance_signals FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
    'CREATE TRIGGER immutable_signal_delete BEFORE DELETE ON compliance_signals FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation()',
)


def upgrade():
    for statement in SQL_STATEMENTS:
        op.execute(statement)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS immutable_signal_evidence ON compliance_signals")
    op.execute("DROP TRIGGER IF EXISTS immutable_signal_delete ON compliance_signals")
    op.execute("DROP TABLE restriction_decisions")
    op.execute("DROP TABLE restriction_exemptions")
    op.execute("DROP TABLE reconciliation_issues")
    op.execute("DROP TABLE employee_changes")
    op.execute("DROP TABLE employee_accounts")
    op.execute("DROP TABLE data_quality_issues")
    op.execute("DROP TABLE communications")
    op.execute("DROP TABLE broker_records")
    op.execute("DROP TABLE auth_sessions")
    op.execute("DROP TABLE worker_heartbeat")
    op.execute("DROP TABLE users")
    op.execute("DROP TABLE stream_failures")
    op.execute("DROP TABLE securities")
    op.execute("DROP TABLE restriction_policies")
    op.execute("DROP TABLE restriction_audit")
    op.execute("DROP TABLE report_exports")
    op.execute("DROP TABLE reconciliation_runs")
    op.execute("DROP TABLE platform_audit")
    op.execute("DROP TABLE login_attempts")
    op.execute("DROP TABLE investigations")
    op.execute("DROP TABLE feed_batches")
    op.execute("DROP TABLE employees")
    op.execute("DROP TABLE employee_feed_issues")
    op.execute("DROP TABLE compliance_signals")
    op.execute("ALTER TABLE trading_events DROP COLUMN source_metadata")
