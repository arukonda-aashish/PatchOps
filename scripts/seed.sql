-- =============================================================================
-- PatchOps Seed Data — Realistic mock data for demo and development
-- Run automatically by Docker on first postgres container start
-- =============================================================================

-- ─── Extensions ──────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Enums ───────────────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE user_role AS ENUM ('user', 'admin');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE cr_status AS ENUM (
    'queued', 'awaiting_approval', 'pending', 'in_progress',
    'completed', 'failed', 'ignored'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE cr_priority AS ENUM ('critical', 'high', 'medium', 'low');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE task_status AS ENUM ('pending', 'running', 'completed', 'failed', 'skipped');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE agent_type AS ENUM ('intake', 'baseline', 'execution', 'validation', 'rca');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE agent_run_status AS ENUM ('running', 'completed', 'failed', 'waiting_approval');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE server_status AS ENUM ('online', 'offline', 'rebooting', 'unknown');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE incident_status AS ENUM ('open', 'in_progress', 'resolved');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ─── Tables ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role user_role NOT NULL DEFAULT 'user',
    team VARCHAR(100),
    timezone VARCHAR(50) DEFAULT 'UTC',
    is_active BOOLEAN DEFAULT TRUE,
    avatar_color VARCHAR(7) DEFAULT '#6366F1',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS servers (
    id SERIAL PRIMARY KEY,
    hostname VARCHAR(255) UNIQUE NOT NULL,
    ip_address VARCHAR(50),
    environment VARCHAR(50) DEFAULT 'production',
    os_version VARCHAR(100),
    timezone VARCHAR(100) DEFAULT 'UTC',
    team VARCHAR(100),
    application VARCHAR(255),
    status server_status DEFAULT 'unknown',
    last_seen_at TIMESTAMPTZ,
    last_reboot_at TIMESTAMPTZ,
    metadata JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS change_requests (
    id SERIAL PRIMARY KEY,
    cr_number VARCHAR(50) UNIQUE NOT NULL,
    title VARCHAR(512) NOT NULL,
    description TEXT,
    status cr_status NOT NULL DEFAULT 'queued',
    priority cr_priority NOT NULL DEFAULT 'medium',
    sn_sys_id VARCHAR(100),
    sn_url VARCHAR(512),
    requested_by VARCHAR(255),
    approver_name VARCHAR(255),
    approver_email VARCHAR(255),
    approved_by VARCHAR(255),
    approved_at TIMESTAMPTZ,
    approved_by_user_id INTEGER REFERENCES users(id),
    change_window_start TIMESTAMPTZ,
    change_window_end TIMESTAMPTZ,
    change_window_timezone VARCHAR(50) DEFAULT 'UTC',
    is_patching BOOLEAN,
    classification_confidence FLOAT,
    classification_reasoning TEXT,
    ordered_server_list JSONB,
    agent1_summary TEXT,
    agent1_accepted BOOLEAN,
    agent1_accepted_by INTEGER REFERENCES users(id),
    agent1_accepted_at TIMESTAMPTZ,
    execution_summary TEXT,
    execution_accepted BOOLEAN,
    validation_report JSONB,
    pre_state JSONB,
    post_state JSONB,
    progress_percent FLOAT DEFAULT 0.0,
    total_servers INTEGER DEFAULT 0,
    completed_servers INTEGER DEFAULT 0,
    failed_servers INTEGER DEFAULT 0,
    received_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS server_tasks (
    id SERIAL PRIMARY KEY,
    cr_id INTEGER NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    server_hostname VARCHAR(255) NOT NULL,
    server_ip VARCHAR(50),
    bucket_number INTEGER DEFAULT 0,
    execution_order INTEGER DEFAULT 0,
    status task_status NOT NULL DEFAULT 'pending',
    reboot_scheduled_for TIMESTAMPTZ,
    pre_state JSONB,
    post_state JSONB,
    health_ok BOOLEAN,
    deviation_percent FLOAT,
    error_message TEXT,
    winrm_logs TEXT,
    requires_service_pause BOOLEAN DEFAULT FALSE,
    service_name VARCHAR(255),
    service_paused_at TIMESTAMPTZ,
    service_resumed_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id SERIAL PRIMARY KEY,
    cr_id INTEGER NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    agent_type agent_type NOT NULL,
    status agent_run_status NOT NULL DEFAULT 'running',
    celery_task_id VARCHAR(255),
    result JSONB,
    error TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id BIGSERIAL PRIMARY KEY,
    cr_id INTEGER NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES agent_runs(id) ON DELETE SET NULL,
    agent_type VARCHAR(50) NOT NULL,
    level VARCHAR(20) DEFAULT 'INFO',
    message TEXT NOT NULL,
    server_hostname VARCHAR(255),
    metadata JSONB,
    ts TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_cr_id ON agent_logs(cr_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_cr_id_id ON agent_logs(cr_id, id);

CREATE TABLE IF NOT EXISTS dependency_edges (
    id SERIAL PRIMARY KEY,
    dependent_server VARCHAR(255) NOT NULL,
    dependency_server VARCHAR(255) NOT NULL,
    reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheduled_reboot_windows (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    timezone VARCHAR(100) NOT NULL,
    preferred_start_time VARCHAR(5) NOT NULL,
    preferred_end_time VARCHAR(5) NOT NULL,
    allowed_days VARCHAR(20) DEFAULT '0,1,2,3,4',
    reason TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS service_pause_configs (
    id SERIAL PRIMARY KEY,
    server_hostname VARCHAR(255) UNIQUE NOT NULL,
    service_name VARCHAR(255) NOT NULL,
    pause_script VARCHAR(512) DEFAULT 'Pause-Service.ps1',
    resume_script VARCHAR(512) DEFAULT 'Resume-Service.ps1',
    reason TEXT,
    pre_pause_wait_seconds INTEGER DEFAULT 5,
    post_resume_wait_seconds INTEGER DEFAULT 10,
    is_active BOOLEAN DEFAULT TRUE,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    cr_id INTEGER REFERENCES change_requests(id),
    server_hostname VARCHAR(255) NOT NULL,
    sn_incident_number VARCHAR(100),
    sn_sys_id VARCHAR(100),
    status incident_status DEFAULT 'open',
    title VARCHAR(512) NOT NULL,
    description TEXT,
    rca_analysis TEXT,
    rca_root_cause TEXT,
    rca_steps TEXT,
    rca_completed_at TIMESTAMPTZ,
    email_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS server_kb_documents (
    id SERIAL PRIMARY KEY,
    server_hostname VARCHAR(255) NOT NULL UNIQUE,
    document_content TEXT NOT NULL,
    last_pre_reboot_script TEXT,
    last_post_reboot_script TEXT,
    last_script_generated_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- SEED DATA
-- =============================================================================

-- ─── Users ────────────────────────────────────────────────────────────────────
-- Passwords: admin123 (bcrypt hashed)
INSERT INTO users (email, full_name, hashed_password, role, team, timezone, avatar_color) VALUES
(
    'sarah.chen@company.com',
    'Sarah Chen',
    '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW', -- password: secret
    'admin', 'Infrastructure Engineering', 'America/New_York', '#6366F1'
),
(
    'james.patel@company.com',
    'James Patel',
    '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW',
    'admin', 'Platform Operations', 'Asia/Kolkata', '#8B5CF6'
),
(
    'maria.silva@company.com',
    'Maria Silva',
    '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW',
    'user', 'Application Development', 'Europe/London', '#EC4899'
),
(
    'alex.kumar@company.com',
    'Alex Kumar',
    '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW',
    'user', 'Database Team', 'Asia/Singapore', '#10B981'
),
(
    'liu.wei@company.com',
    'Liu Wei',
    '$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW',
    'user', 'Cloud Infrastructure', 'Asia/Shanghai', '#F59E0B'
)
ON CONFLICT (email) DO NOTHING;

-- ─── Servers ──────────────────────────────────────────────────────────────────
INSERT INTO servers (hostname, ip_address, environment, os_version, timezone, team, application, status) VALUES
('srv-db-01',    '10.0.1.10', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Database Team',    'PostgreSQL Primary',    'online'),
('srv-db-02',    '10.0.1.11', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Database Team',    'PostgreSQL Replica',    'online'),
('srv-db-03',    '10.0.1.12', 'production',  'Windows Server 2019 Datacenter','Eastern Standard Time', 'Database Team',    'MSSQL Analytics',       'online'),
('srv-app-01',   '10.0.2.10', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Platform Ops',     'API Gateway',           'online'),
('srv-app-02',   '10.0.2.11', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Platform Ops',     'Microservices Host',    'online'),
('srv-app-03',   '10.0.2.12', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Platform Ops',     'Microservices Host',    'online'),
('srv-web-01',   '10.0.3.10', 'production',  'Windows Server 2022 Standard', 'Pacific Standard Time',  'Platform Ops',     'Web Frontend (IIS)',    'online'),
('srv-web-02',   '10.0.3.11', 'production',  'Windows Server 2022 Standard', 'Pacific Standard Time',  'Platform Ops',     'Web Frontend (IIS)',    'online'),
('srv-cache-01', '10.0.4.10', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Infrastructure',   'Redis Cache',           'online'),
('srv-mq-01',    '10.0.5.10', 'production',  'Windows Server 2019 Standard', 'India Standard Time',    'Infrastructure',   'RabbitMQ Broker',       'online'),
('srv-monitor',  '10.0.6.10', 'production',  'Windows Server 2022 Standard', 'UTC',                    'Infrastructure',   'Monitoring Stack',      'online'),
('srv-dev-01',   '10.1.1.10', 'development', 'Windows Server 2022 Standard', 'UTC',                    'App Dev',          'Dev Environment',       'online')
ON CONFLICT (hostname) DO NOTHING;

-- ─── Dependency Graph ─────────────────────────────────────────────────────────
-- Database servers must be rebooted before app servers
-- App servers before web servers
-- Cache before app servers (apps depend on cache)
INSERT INTO dependency_edges (dependent_server, dependency_server, reason, created_by) VALUES
('srv-app-01',   'srv-db-01',    'App server connects to primary DB on startup', 1),
('srv-app-02',   'srv-db-01',    'App server connects to primary DB on startup', 1),
('srv-app-03',   'srv-db-02',    'App server uses DB replica for read queries',  1),
('srv-app-01',   'srv-cache-01', 'App caches session data in Redis at startup',  1),
('srv-app-02',   'srv-cache-01', 'App caches session data in Redis at startup',  1),
('srv-web-01',   'srv-app-01',   'Web tier proxies to app-01 as primary backend', 1),
('srv-web-01',   'srv-app-02',   'Web tier proxies to app-02 as failover backend', 1),
('srv-web-02',   'srv-app-01',   'Web tier proxies to app-01 as primary backend', 1),
('srv-web-02',   'srv-app-03',   'Web tier load balances to app-03',             1),
('srv-app-01',   'srv-mq-01',    'App subscribes to message queues on startup',  1)
ON CONFLICT DO NOTHING;

-- ─── Scheduled Reboot Windows ─────────────────────────────────────────────────
INSERT INTO scheduled_reboot_windows (name, description, timezone, preferred_start_time, preferred_end_time, allowed_days, reason, created_by) VALUES
(
    'APAC Maintenance Window',
    'Servers in India/Singapore timezone — reboot during APAC low-traffic hours',
    'India Standard Time',
    '02:00', '05:00',
    '5,6',  -- Saturday, Sunday
    'APAC traffic is lowest between 2-5 AM IST on weekends',
    1
),
(
    'US-East Maintenance Window',
    'US East Coast servers — maintenance during off-peak weekend hours',
    'Eastern Standard Time',
    '01:00', '04:00',
    '5,6',
    'EST traffic nadir is 1-4 AM on weekends',
    1
),
(
    'US-West Maintenance Window',
    'US West Coast servers — late-night window',
    'Pacific Standard Time',
    '23:00', '02:00',
    '5,6',
    'PST servers serve EU morning traffic — late Saturday PST avoids overlap',
    1
),
(
    'UTC Weeknight Window',
    'UTC servers — weeknight low-traffic window',
    'UTC',
    '02:00', '06:00',
    '0,1,2,3,4',
    'UTC servers are primarily EMEA region — low traffic after midnight UTC',
    1
)
ON CONFLICT DO NOTHING;

-- ─── Service Pause Configs ────────────────────────────────────────────────────
INSERT INTO service_pause_configs (server_hostname, service_name, pause_script, resume_script, reason, pre_pause_wait_seconds, post_resume_wait_seconds, created_by) VALUES
('srv-db-01',    'PostgreSQL',      'Pause-Service.ps1', 'Resume-Service.ps1',
 'Primary DB must be stopped cleanly to prevent WAL corruption during reboot', 15, 30, 1),
('srv-db-02',    'PostgreSQL',      'Pause-Service.ps1', 'Resume-Service.ps1',
 'Replica needs graceful shutdown to avoid replication slot issues', 10, 20, 1),
('srv-mq-01',    'RabbitMQ',        'Pause-Service.ps1', 'Resume-Service.ps1',
 'Message broker needs clean shutdown to persist unprocessed messages', 10, 15, 1),
('srv-cache-01', 'Redis',           'Pause-Service.ps1', 'Resume-Service.ps1',
 'Redis BGSAVE should complete before shutdown to prevent data loss', 8, 10, 1),
('srv-app-01',   'PatchOpsApp',     'Pause-Service.ps1', 'Resume-Service.ps1',
 'Drain in-flight requests before restart to prevent 503 errors', 5, 10, 1)
ON CONFLICT (server_hostname) DO NOTHING;

-- ─── Change Requests (realistic mix of statuses) ──────────────────────────────

-- CR 1: Completed — Q4 Security Patch
INSERT INTO change_requests (
    cr_number, title, description, status, priority, sn_sys_id,
    requested_by, approver_name, approver_email,
    approved_by, approved_at,
    change_window_start, change_window_end, change_window_timezone,
    is_patching, classification_confidence, classification_reasoning,
    ordered_server_list, agent1_summary, agent1_accepted, agent1_accepted_at,
    execution_summary, execution_accepted,
    validation_report, pre_state, post_state,
    progress_percent, total_servers, completed_servers, failed_servers,
    received_at, started_at, completed_at
) VALUES (
    'CHG0010001',
    'Q4 2024 Security Patch Deployment — Critical CVE Remediation',
    'Deployment of Microsoft Security Updates addressing CVE-2024-38080, CVE-2024-38061, and CVE-2024-38094 across production Windows infrastructure.',
    'completed', 'critical',
    'sys_id_chg0010001',
    'Maria Silva', 'Sarah Chen', 'sarah.chen@company.com',
    'Sarah Chen', NOW() - INTERVAL '5 days',
    NOW() - INTERVAL '4 days 20 hours',
    NOW() - INTERVAL '4 days 16 hours',
    'UTC',
    TRUE, 0.97,
    'Title explicitly mentions security patch deployment. Description references CVE numbers which are always security-related patching activity.',
    '{"servers":["srv-db-01","srv-db-02","srv-cache-01","srv-mq-01","srv-app-01","srv-app-02","srv-app-03","srv-web-01","srv-web-02"],"buckets":[["srv-db-01","srv-db-02"],["srv-cache-01","srv-mq-01"],["srv-app-01","srv-app-02","srv-app-03"],["srv-web-01","srv-web-02"]],"dependency_notes":["srv-app-01 depends on srv-db-01 (App server connects to primary DB on startup)","srv-app-01 depends on srv-cache-01 (App caches session data in Redis at startup)","srv-web-01 depends on srv-app-01 (Web tier proxies to app-01 as primary backend)"],"pause_servers":["srv-db-01","srv-db-02","srv-cache-01","srv-mq-01","srv-app-01"],"reasoning":["Topological sort resolved 10 dependency edges","Servers in same bucket execute in parallel (max 5)","5 servers require service pause/resume"]}',
    'The execution plan processes 9 servers across 4 dependency-ordered buckets. Database servers (Bucket 1) boot first as all app-tier servers depend on them at startup. Cache and MQ (Bucket 2) follow since app servers depend on Redis for session state. Application servers (Bucket 3) proceed in parallel once their dependencies are healthy. Web tier (Bucket 4) completes the sequence as the outermost layer.',
    TRUE, NOW() - INTERVAL '4 days 19 hours',
    'All 9 servers rebooted successfully across 4 execution buckets. Total execution time: 47 minutes. No service disruptions detected.',
    TRUE,
    '{"results":[{"hostname":"srv-db-01","health_ok":true,"deviation_percent":1.2},{"hostname":"srv-db-02","health_ok":true,"deviation_percent":0.8},{"hostname":"srv-app-01","health_ok":true,"deviation_percent":2.1},{"hostname":"srv-web-01","health_ok":true,"deviation_percent":1.5}],"total":9,"healthy":9,"unhealthy":0}',
    '{"srv-db-01":{"Services":52,"CPU":12.3,"MemoryGB":64,"FreeMemoryGB":42.1}}',
    '{"srv-db-01":{"Services":54,"CPU":8.7,"MemoryGB":64,"FreeMemoryGB":44.2}}',
    100.0, 9, 9, 0,
    NOW() - INTERVAL '5 days',
    NOW() - INTERVAL '4 days 19 hours',
    NOW() - INTERVAL '4 days 15 hours'
);

-- CR 2: In Progress
INSERT INTO change_requests (
    cr_number, title, description, status, priority,
    requested_by, approver_name, approver_email,
    approved_by, approved_at,
    change_window_start, change_window_end, change_window_timezone,
    is_patching, classification_confidence, classification_reasoning,
    ordered_server_list, agent1_summary, agent1_accepted, agent1_accepted_at,
    execution_summary,
    progress_percent, total_servers, completed_servers, failed_servers,
    received_at, started_at
) VALUES (
    'CHG0010002',
    'January 2025 Cumulative Update — KB5034441 Production Rollout',
    'Rolling deployment of Windows Server Cumulative Update KB5034441 addressing 71 security vulnerabilities across production server fleet.',
    'in_progress', 'high',
    'Alex Kumar', 'James Patel', 'james.patel@company.com',
    'James Patel', NOW() - INTERVAL '2 hours',
    NOW() - INTERVAL '1 hour',
    NOW() + INTERVAL '3 hours',
    'UTC',
    TRUE, 0.95,
    'KB5034441 is a known Windows Cumulative Update identifier. Title confirms this is a production patch rollout.',
    '{"servers":["srv-db-01","srv-db-02","srv-cache-01","srv-app-01","srv-app-02","srv-web-01"],"buckets":[["srv-db-01","srv-db-02"],["srv-cache-01"],["srv-app-01","srv-app-02"],["srv-web-01"]],"dependency_notes":["srv-app-01 depends on srv-db-01","srv-web-01 depends on srv-app-01"],"pause_servers":["srv-db-01","srv-db-02","srv-cache-01"],"reasoning":["6 servers in 4 sequential buckets","3 servers require service pause/resume"]}',
    'Plan covers 6 servers in 4 dependency-ordered buckets. Database tier bootstraps first (Bucket 1), followed by cache layer (Bucket 2), then dual app servers in parallel (Bucket 3), and finally the web server (Bucket 4). Three servers require controlled service pauses to prevent data loss.',
    TRUE, NOW() - INTERVAL '90 minutes',
    NULL,
    62.5, 6, 4, 0,
    NOW() - INTERVAL '3 hours',
    NOW() - INTERVAL '90 minutes'
);

-- CR 3: Awaiting Approval
INSERT INTO change_requests (
    cr_number, title, description, status, priority,
    requested_by, approver_name, approver_email,
    change_window_start, change_window_end, change_window_timezone,
    is_patching, classification_confidence, classification_reasoning,
    received_at
) VALUES (
    'CHG0010003',
    'February Patch Tuesday — Windows Server Security Updates Batch',
    'Monthly Patch Tuesday deployment covering Microsoft security advisories MS25-001 through MS25-012. Includes critical patches for Windows Remote Desktop Services and SMB protocol vulnerabilities.',
    'awaiting_approval', 'critical',
    'Liu Wei', 'Sarah Chen', 'sarah.chen@company.com',
    NOW() + INTERVAL '3 days',
    NOW() + INTERVAL '3 days 4 hours',
    'UTC',
    TRUE, 0.99,
    'This is explicitly identified as a Patch Tuesday deployment with Microsoft security advisories. High confidence patching classification.',
    NOW() - INTERVAL '1 hour'
);

-- CR 4: Awaiting Approval — High priority
INSERT INTO change_requests (
    cr_number, title, description, status, priority,
    requested_by, approver_name, approver_email,
    change_window_start, change_window_end, change_window_timezone,
    is_patching, classification_confidence, classification_reasoning,
    received_at
) VALUES (
    'CHG0010004',
    'Emergency Hotfix — Log4Shell Variant CVE-2025-44228 Critical Patch',
    'Emergency deployment of hotfix addressing newly discovered critical Log4j variant. CVSS score 10.0. Requires immediate remediation on all production servers.',
    'awaiting_approval', 'critical',
    'Maria Silva', 'James Patel', 'james.patel@company.com',
    NOW() + INTERVAL '6 hours',
    NOW() + INTERVAL '10 hours',
    'UTC',
    TRUE, 0.99,
    'Emergency hotfix for a critical CVE with maximum CVSS score. Definitive patching classification.',
    NOW() - INTERVAL '30 minutes'
);

-- CR 5: Pending (approved, waiting for change window)
INSERT INTO change_requests (
    cr_number, title, description, status, priority,
    requested_by, approver_name, approver_email,
    approved_by, approved_at,
    change_window_start, change_window_end, change_window_timezone,
    is_patching, classification_confidence, classification_reasoning,
    received_at
) VALUES (
    'CHG0010005',
    'APAC Region — Q1 OS Hardening and Security Patches',
    'Quarterly security hardening including CIS benchmark patches, Windows Defender signature updates, and OS-level security configuration updates for APAC production servers.',
    'pending', 'medium',
    'Alex Kumar', 'Sarah Chen', 'sarah.chen@company.com',
    'Sarah Chen', NOW() - INTERVAL '1 day',
    NOW() + INTERVAL '1 day 2 hours',
    NOW() + INTERVAL '1 day 6 hours',
    'India Standard Time',
    TRUE, 0.91,
    'Security hardening with patch updates is classified as patching activity. OS-level security patches are core patching work.',
    NOW() - INTERVAL '2 days'
);

-- CR 6: Failed
INSERT INTO change_requests (
    cr_number, title, description, status, priority,
    requested_by, approver_name, approver_email,
    approved_by, approved_at,
    change_window_start, change_window_end, change_window_timezone,
    is_patching, classification_confidence,
    progress_percent, total_servers, completed_servers, failed_servers,
    validation_report,
    received_at, started_at, completed_at
) VALUES (
    'CHG0010006',
    'November Patch Cycle — KB5031364 Rollout (Partial Failure)',
    'Windows cumulative update KB5031364. One server encountered compatibility issue with legacy application.',
    'failed', 'high',
    'Liu Wei', 'James Patel', 'james.patel@company.com',
    'James Patel', NOW() - INTERVAL '10 days',
    NOW() - INTERVAL '9 days 20 hours',
    NOW() - INTERVAL '9 days 16 hours',
    'UTC',
    TRUE, 0.93,
    83.3, 6, 5, 1,
    '{"results":[{"hostname":"srv-db-03","health_ok":false,"deviation_percent":34.2},{"hostname":"srv-app-01","health_ok":true,"deviation_percent":1.1}],"total":6,"healthy":5,"unhealthy":1}',
    NOW() - INTERVAL '10 days',
    NOW() - INTERVAL '9 days 19 hours',
    NOW() - INTERVAL '9 days 18 hours'
);

-- ─── Server Tasks for In-Progress CR ─────────────────────────────────────────
DO $$
DECLARE v_cr_id INTEGER;
BEGIN
    SELECT id INTO v_cr_id FROM change_requests WHERE cr_number = 'CHG0010002';
    IF v_cr_id IS NOT NULL THEN
        INSERT INTO server_tasks (cr_id, server_hostname, bucket_number, execution_order, status, health_ok, deviation_percent, started_at, completed_at)
        VALUES
        (v_cr_id, 'srv-db-01',    0, 0, 'completed', TRUE,  1.2, NOW() - INTERVAL '80 min', NOW() - INTERVAL '65 min'),
        (v_cr_id, 'srv-db-02',    0, 1, 'completed', TRUE,  0.9, NOW() - INTERVAL '80 min', NOW() - INTERVAL '63 min'),
        (v_cr_id, 'srv-cache-01', 1, 0, 'completed', TRUE,  2.1, NOW() - INTERVAL '60 min', NOW() - INTERVAL '48 min'),
        (v_cr_id, 'srv-app-01',   2, 0, 'running',  NULL,  NULL, NOW() - INTERVAL '10 min', NULL),
        (v_cr_id, 'srv-app-02',   2, 1, 'running',  NULL,  NULL, NOW() - INTERVAL '8 min',  NULL),
        (v_cr_id, 'srv-web-01',   3, 0, 'pending',  NULL,  NULL, NULL, NULL)
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- ─── Agent Logs for In-Progress CR ───────────────────────────────────────────
DO $$
DECLARE
    v_cr_id INTEGER;
    v_run_id INTEGER;
BEGIN
    SELECT id INTO v_cr_id FROM change_requests WHERE cr_number = 'CHG0010002';
    IF v_cr_id IS NOT NULL THEN
        INSERT INTO agent_runs (cr_id, agent_type, status, started_at, completed_at)
        VALUES (v_cr_id, 'baseline', 'completed', NOW() - INTERVAL '95 min', NOW() - INTERVAL '82 min')
        RETURNING id INTO v_run_id;

        INSERT INTO agent_logs (cr_id, run_id, agent_type, level, message, server_hostname, ts) VALUES
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '🤖 Agent 1 (Baseline) started for CHG0010002', NULL, NOW() - INTERVAL '95 min'),
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '📎 Fetching server list from ServiceNow attachment...', NULL, NOW() - INTERVAL '94 min'),
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '✅ Found 6 servers: srv-db-01, srv-db-02, srv-cache-01, srv-app-01, srv-app-02, srv-web-01', NULL, NOW() - INTERVAL '93 min'),
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '🔗 Loading dependency graph from knowledge base...', NULL, NOW() - INTERVAL '92 min'),
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '⏸️ Checking service pause requirements...', NULL, NOW() - INTERVAL '91 min'),
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '🕒 Checking timezone-specific reboot window constraints...', NULL, NOW() - INTERVAL '90 min'),
        (v_cr_id, v_run_id, 'baseline', 'INFO',    '📦 Created 4 execution buckets: B1:[srv-db-01,srv-db-02] | B2:[srv-cache-01] | B3:[srv-app-01,srv-app-02] | B4:[srv-web-01]', NULL, NOW() - INTERVAL '89 min'),
        (v_cr_id, v_run_id, 'baseline', 'SUCCESS', '✅ Baseline complete — 6 servers in 4 buckets. Awaiting user approval.', NULL, NOW() - INTERVAL '82 min');

        INSERT INTO agent_runs (cr_id, agent_type, status, started_at)
        VALUES (v_cr_id, 'execution', 'running', NOW() - INTERVAL '81 min')
        RETURNING id INTO v_run_id;

        INSERT INTO agent_logs (cr_id, run_id, agent_type, level, message, server_hostname, ts) VALUES
        (v_cr_id, v_run_id, 'execution', 'INFO',    '🚀 Agent 2 (Execution) started — beginning server reboots', NULL, NOW() - INTERVAL '81 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '📦 Processing Bucket 1/4: srv-db-01, srv-db-02', NULL, NOW() - INTERVAL '81 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '⏸️ Pausing service on srv-db-01', 'srv-db-01', NOW() - INTERVAL '80 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '🔄 Initiating reboot on srv-db-01', 'srv-db-01', NOW() - INTERVAL '79 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '⏳ Waiting for srv-db-01 to come back online...', 'srv-db-01', NOW() - INTERVAL '79 min'),
        (v_cr_id, v_run_id, 'execution', 'SUCCESS', '✅ srv-db-01 rebooted successfully', 'srv-db-01', NOW() - INTERVAL '65 min'),
        (v_cr_id, v_run_id, 'execution', 'SUCCESS', '✅ srv-db-02 rebooted successfully', 'srv-db-02', NOW() - INTERVAL '63 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '📦 Processing Bucket 2/4: srv-cache-01', NULL, NOW() - INTERVAL '62 min'),
        (v_cr_id, v_run_id, 'execution', 'SUCCESS', '✅ srv-cache-01 rebooted successfully', 'srv-cache-01', NOW() - INTERVAL '48 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '📦 Processing Bucket 3/4: srv-app-01, srv-app-02', NULL, NOW() - INTERVAL '12 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '🔄 Initiating reboot on srv-app-01', 'srv-app-01', NOW() - INTERVAL '11 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '⏳ Waiting for srv-app-01 to come back online...', 'srv-app-01', NOW() - INTERVAL '10 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '🔄 Initiating reboot on srv-app-02', 'srv-app-02', NOW() - INTERVAL '10 min'),
        (v_cr_id, v_run_id, 'execution', 'INFO',    '⏳ Waiting for srv-app-02 to come back online...', 'srv-app-02', NOW() - INTERVAL '8 min');

    END IF;
END $$;

-- ─── Incident for Failed CR ───────────────────────────────────────────────────
DO $$
DECLARE v_cr_id INTEGER;
BEGIN
    SELECT id INTO v_cr_id FROM change_requests WHERE cr_number = 'CHG0010006';
    IF v_cr_id IS NOT NULL THEN
        INSERT INTO incidents (
            cr_id, server_hostname, sn_incident_number, sn_sys_id, status,
            title, description,
            rca_analysis, rca_root_cause, rca_steps, rca_completed_at, email_sent, created_at
        ) VALUES (
            v_cr_id, 'srv-db-03', 'INC0020001', 'sys_inc_0020001', 'in_progress',
            'srv-db-03 failed to come back online after KB5031364 patch — service deviation 34.2%',
            'Server srv-db-03 failed health validation after reboot. Pre-reboot: 48 services running. Post-reboot: 31 services running. Deviation: 34.2% exceeds threshold of 15%.',
            'Post-reboot analysis reveals that 17 Windows services failed to start after applying KB5031364. Root investigation shows a compatibility conflict between the update''s new SMB signing requirements and the legacy MSSQL Analytics service configuration. The service account used by MSSQL Analytics lacks the updated permissions required by the patched SMB stack, causing authentication failures during service startup.',
            'KB5031364 introduced mandatory SMB signing that conflicts with the MSSQL Analytics service account configuration. The service account (svc-mssql-analytics) uses NTLM authentication over SMB without message signing, which the patch disables by default.',
            E'1. Connect to srv-db-03 via RDP\n2. Check Windows Event Log: Application and System logs for service start failures\n3. Run: Get-Service | Where-Object {$_.Status -eq "Stopped" -and $_.StartType -eq "Automatic"}\n4. Update MSSQL Analytics service account to use Kerberos or enable SMB signing compatibility mode\n5. Apply registry fix: HKLM\\SYSTEM\\CurrentControlSet\\Services\\LanmanWorkstation\\Parameters\\RequireSecuritySignature = 0 (temporary)\n6. Restart affected services: Restart-Service "MSSQL Analytics" -Force\n7. Verify all 48 services return to running state\n8. Apply permanent fix: Update service account to domain admin equivalent or configure SMB signing properly',
            NOW() - INTERVAL '9 days 17 hours', TRUE,
            NOW() - INTERVAL '9 days 18 hours'
        ) ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- ─── Verify ───────────────────────────────────────────────────────────────────
DO $$
BEGIN
    RAISE NOTICE 'Seed complete: % users, % servers, % CRs, % dependency edges',
        (SELECT COUNT(*) FROM users),
        (SELECT COUNT(*) FROM servers),
        (SELECT COUNT(*) FROM change_requests),
        (SELECT COUNT(*) FROM dependency_edges);
END $$;
