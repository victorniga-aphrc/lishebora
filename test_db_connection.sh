#!/bin/bash
# Test database connection with password authentication

echo "Testing connection via TCP/IP (localhost) with password..."
psql -h localhost -U postgres -d lishebora -c "SELECT version();"

echo ""
echo "If that worked, your .env should have:"
echo "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/lishebora"
