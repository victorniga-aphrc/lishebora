#!/bin/bash
# Script to fix PostgreSQL authentication

echo "Setting password for postgres user..."
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"

echo ""
echo "Password set! Now update your .env file with:"
echo "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/lishebora"
