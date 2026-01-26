# Docker Setup Guide

This guide explains how to run the Lishebora backend using Docker and Docker Compose.

## Prerequisites

- **Docker** installed (version 20.10 or later)
- **Docker Compose** installed (version 2.0 or later)

### Verify Installation

```bash
docker --version
docker-compose --version
```

## Quick Start

### 1. Set Up Environment Variables

Create a `.env` file in the project root (if not already present):

```bash
# Replicate API Token (required)
REPLICATE_API_TOKEN=your_replicate_api_token_here

# Optional: Override default model
REPLICATE_MODEL=openai/gpt-4.1-mini

# Database URL (automatically set by docker-compose, but can override)
# DATABASE_URL=postgresql://postgres:postgres@db:5432/lishebora
```

**Important**: The `REPLICATE_API_TOKEN` is required for the OCR functionality to work.

### 2. Build and Start Services

```bash
# Build and start all services (database + API)
docker-compose up -d

# View logs
docker-compose logs -f

# Or start in foreground to see logs
docker-compose up
```

This will:
- Start PostgreSQL database container (`lishebora_db`)
- Build and start FastAPI application container (`lishebora_api`)
- Create a Docker network for service communication
- Create a persistent volume for database data

### 3. Run Database Migrations

After the containers are running, you need to run Alembic migrations:

```bash
# Run migrations inside the API container
docker-compose exec api alembic upgrade head
```

### 4. Access the Application

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health

## Common Commands

### Start Services

```bash
# Start in background
docker-compose up -d

# Start in foreground (see logs)
docker-compose up
```

### Stop Services

```bash
# Stop services
docker-compose stop

# Stop and remove containers
docker-compose down

# Stop, remove containers, and remove volumes (⚠️ deletes database data)
docker-compose down -v
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f api
docker-compose logs -f db
```

### Execute Commands in Containers

```bash
# Run migrations
docker-compose exec api alembic upgrade head

# Create new migration
docker-compose exec api alembic revision --autogenerate -m "Description"

# Access database directly
docker-compose exec db psql -U postgres -d lishebora

# Access API container shell
docker-compose exec api bash
```

### Rebuild After Code Changes

```bash
# Rebuild and restart
docker-compose up -d --build

# Rebuild without cache
docker-compose build --no-cache
docker-compose up -d
```

## Development Workflow

### Option 1: Development with Volume Mounting (Recommended)

The `docker-compose.yml` is configured to mount the `app` directory, so code changes are reflected immediately:

```bash
# Start services
docker-compose up -d

# Make code changes in your editor
# Changes are automatically reflected (uvicorn --reload is enabled)

# Restart if needed
docker-compose restart api
```

### Option 2: Rebuild After Changes

```bash
# After making changes
docker-compose up -d --build api
```

## Production Considerations

For production deployment, you should:

1. **Remove volume mounts** from `docker-compose.yml` (lines with `volumes:` under `api` service)
2. **Use environment-specific `.env` files** or secrets management
3. **Set up proper logging** (file-based or centralized)
4. **Configure reverse proxy** (nginx, Traefik, etc.)
5. **Set up SSL/TLS certificates**
6. **Use production-grade database** (managed PostgreSQL service)
7. **Configure resource limits** in `docker-compose.yml`:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

## Troubleshooting

### Database Connection Issues

If the API can't connect to the database:

1. **Check if database is healthy**:
   ```bash
   docker-compose ps
   ```

2. **Check database logs**:
   ```bash
   docker-compose logs db
   ```

3. **Verify database is accessible**:
   ```bash
   docker-compose exec db pg_isready -U postgres
   ```

4. **Check environment variables**:
   ```bash
   docker-compose exec api env | grep DATABASE_URL
   ```

### Migration Issues

If migrations fail:

1. **Check database connection**:
   ```bash
   docker-compose exec api python -c "from app.db import engine; print(engine.connect())"
   ```

2. **Manually run migrations**:
   ```bash
   docker-compose exec api alembic upgrade head
   ```

3. **Check migration status**:
   ```bash
   docker-compose exec api alembic current
   ```

### API Not Starting

1. **Check logs**:
   ```bash
   docker-compose logs api
   ```

2. **Verify environment variables**:
   ```bash
   docker-compose exec api env | grep REPLICATE
   ```

3. **Test API health**:
   ```bash
   curl http://localhost:8000/health
   ```

### Port Already in Use

If port 8000 or 5432 is already in use:

1. **Change ports in `docker-compose.yml`**:
   ```yaml
   services:
     api:
       ports:
         - "8001:8000"  # Change 8000 to 8001
     db:
       ports:
         - "5433:5432"  # Change 5432 to 5433
   ```

2. **Update `DATABASE_URL`** if you changed the database port

## Data Persistence

Database data is stored in a Docker volume named `postgres_data`. This means:

- **Data persists** even if you stop/remove containers
- **To reset database**: `docker-compose down -v` (⚠️ deletes all data)
- **To backup**: Use `pg_dump` inside the container

### Backup Database

```bash
# Create backup
docker-compose exec db pg_dump -U postgres lishebora > backup.sql

# Restore backup
docker-compose exec -T db psql -U postgres lishebora < backup.sql
```

## Clean Up

To completely remove everything:

```bash
# Stop and remove containers, networks, and volumes
docker-compose down -v

# Remove images (optional)
docker rmi lishebora_vic-api
docker rmi postgres:14-alpine
```

## Architecture

```
┌─────────────────┐
│   Docker Host   │
│                 │
│  ┌───────────┐  │
│  │   API     │  │  ← FastAPI Application
│  │  :8000    │  │
│  └─────┬─────┘  │
│        │        │
│  ┌─────▼─────┐  │
│  │    DB     │  │  ← PostgreSQL Database
│  │   :5432   │  │
│  └───────────┘  │
│                 │
│  lishebora_network │
└─────────────────┘
```

## Next Steps

- Set up CI/CD pipeline
- Configure monitoring and logging
- Set up automated backups
- Deploy to cloud platform (AWS, GCP, Azure, etc.)
