-- Auth, tenancy, and memory session registry
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(128) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(320) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL,
    tenant_id VARCHAR(128) REFERENCES tenants(id),
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);
CREATE INDEX IF NOT EXISTS ix_users_role ON users(role);
CREATE INDEX IF NOT EXISTS ix_users_tenant ON users(tenant_id);

CREATE TABLE IF NOT EXISTS memory_sessions (
    session_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128),
    user_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_memory_sessions_tenant ON memory_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_memory_sessions_user ON memory_sessions(user_id);
