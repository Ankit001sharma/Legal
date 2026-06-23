-- Create the Java backend database alongside the Python platform database.
-- The 'legalai' database is already created by POSTGRES_DB env var.
SELECT 'CREATE DATABASE legalai_java'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'legalai_java')\gexec

GRANT ALL PRIVILEGES ON DATABASE legalai_java TO legalai;
