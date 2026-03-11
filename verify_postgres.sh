#!/bin/bash
# Quick script to verify PostgreSQL is running

echo "Checking PostgreSQL status..."
sudo service postgresql status

echo ""
echo "Testing connection..."
pg_isready

echo ""
echo "Testing database connection..."
psql -U postgres -c "SELECT version();" 2>&1

echo ""
echo "Listing databases..."
psql -U postgres -l 2>&1
