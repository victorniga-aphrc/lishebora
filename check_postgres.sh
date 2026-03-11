#!/bin/bash
# Check if PostgreSQL server is actually running

echo "=== Checking PostgreSQL processes ==="
ps aux | grep postgres | grep -v grep

echo ""
echo "=== Testing connection ==="
pg_isready

echo ""
echo "=== Checking PostgreSQL cluster status ==="
sudo systemctl status postgresql@*-main 2>/dev/null || echo "No specific cluster found"

echo ""
echo "=== Trying to connect ==="
psql -U postgres -c "SELECT version();" 2>&1
