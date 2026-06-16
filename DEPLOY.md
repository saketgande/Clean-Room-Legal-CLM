# AEGIS Legal CLM - Ubuntu VM Deployment, No Docker

> Prefer containers? See [DOCKER.md](DOCKER.md) for running the whole stack
> (Postgres, Redis, API, worker, beat, frontend) with `docker compose`. This
> guide is the native, no-Docker path.

This guide runs the whole application directly on an Ubuntu VM:

- Nginx listens on ports 80/443.
- Next.js frontend listens on `127.0.0.1:3000`.
- FastAPI backend listens on `127.0.0.1:8000`.
- Celery worker and Celery Beat run as systemd services.
- PostgreSQL with pgvector stores app data.
- Redis backs Celery and rate limiting.
- Contract files are stored on the VM filesystem.

The examples assume:

- App path: `/opt/aegis`
- Backend env file: `/etc/aegis/backend.env`
- Contract storage: `/var/lib/aegis/contracts`
- Domain: `aegis.ctpsandbox.com`
- Linux user: `aegis`

Replace those values for your VM.

## 1. Install OS Packages

Use Ubuntu 24.04 if possible. The backend requires Python 3.11 or newer; if
you use Ubuntu 22.04, install Python 3.11/3.12 from your approved package
source instead of the default Python 3.10. Install Node.js 22 LTS or newer from
your approved package source.

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential curl git nginx redis-server libmagic1 \
  python3 python3-venv python3-pip \
  postgresql postgresql-contrib
```

Install pgvector for your PostgreSQL version. If your package repo provides it:

```bash
sudo apt-get install -y postgresql-16-pgvector
```

If the package name differs on your VM, install the pgvector package matching the PostgreSQL major version shown by:

```bash
psql --version
```

Install Node.js and npm, then verify:

```bash
node --version
npm --version
```

## 2. Create the App User and Directories

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin aegis
sudo mkdir -p /opt/aegis /etc/aegis /var/lib/aegis/contracts
sudo chown -R aegis:aegis /opt/aegis /var/lib/aegis
sudo chown root:aegis /etc/aegis
sudo chmod 750 /etc/aegis
```

Copy or clone this repo into `/opt/aegis`.

```bash
sudo git clone <repo-url> /opt/aegis
sudo chown -R aegis:aegis /opt/aegis
```

## 3. Configure PostgreSQL

Create a strong password first.

```bash
openssl rand -hex 24
```

Then create the database, user, and pgvector extension:

```bash
sudo -u postgres psql
```

```sql
CREATE USER legal_clm WITH PASSWORD 'replace-with-strong-password';
CREATE DATABASE legal_clm OWNER legal_clm;
\c legal_clm
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

## 4. Configure Backend Environment

Create `/etc/aegis/backend.env` from the template:

```bash
sudo cp /opt/aegis/deploy/backend.env.example /etc/aegis/backend.env
sudo chmod 640 /etc/aegis/backend.env
sudo chown root:aegis /etc/aegis/backend.env
sudo nano /etc/aegis/backend.env
```

Minimum values to change:

- `DATABASE_URL`
- `SECRET_KEY`
- `SETUP_TOKEN`
- `APP_BASE_URL`
- `ALLOWED_HOSTS`
- `CORS_ORIGINS`
- real integration keys when `ENVIRONMENT=production`

Generate strong secrets with:

```bash
openssl rand -hex 32   # SECRET_KEY
openssl rand -hex 32   # SETUP_TOKEN
```

`ENVIRONMENT` is required. For an HTTPS VM that is still a development/sandbox
deployment with mock integrations enabled, explicitly use development mode:

```dotenv
ENVIRONMENT=development
ALLOWED_HOSTS=aegis.ctpsandbox.com,localhost,127.0.0.1
CORS_ORIGINS=https://aegis.ctpsandbox.com
FORCE_HTTPS=false
REFRESH_COOKIE_SECURE=true
EXPOSE_REFRESH_TOKEN_IN_BODY=false
EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE=false
APP_BASE_URL=https://aegis.ctpsandbox.com
```

Use `ENVIRONMENT=production` only after real secrets and live integrations are
configured and the `MOCK_*` flags are disabled:

```dotenv
ENVIRONMENT=production
ALLOWED_HOSTS=aegis.ctpsandbox.com
CORS_ORIGINS=https://aegis.ctpsandbox.com
FORCE_HTTPS=false
REFRESH_COOKIE_SECURE=true
EXPOSE_REFRESH_TOKEN_IN_BODY=false
EXPOSE_PASSWORD_RESET_TOKEN_IN_RESPONSE=false
MOCK_CLAUDE=false
MOCK_DOCUSIGN=false
MOCK_REDUCTO=false
MOCK_RESEND=false
APP_BASE_URL=https://aegis.ctpsandbox.com
```

`FORCE_HTTPS=false` is intentional for the VM setup because Nginx terminates TLS.
Configure the HTTP-to-HTTPS redirect and HSTS headers in Nginx or through your
certificate automation.

For an internal/local VM without HTTPS, use:

```dotenv
ENVIRONMENT=development
APP_BASE_URL=http://<vm-ip-or-hostname>
REFRESH_COOKIE_SECURE=false
```

## 5. Install Backend Dependencies

```bash
sudo -u aegis bash
cd /opt/aegis/backend
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
exit
```

## 6. Build the Frontend

The frontend should call the API through Nginx on the same origin.

```bash
sudo -u aegis bash
cd /opt/aegis/frontend
cp .env.production.example .env.production
npm ci
npm run build
exit
```

## 7. Run Database Migrations

```bash
sudo -u aegis bash
cd /opt/aegis/backend
set -a
. /etc/aegis/backend.env
set +a
. .venv/bin/activate
alembic upgrade head
exit
```

## 8. Install systemd Services

```bash
sudo cp /opt/aegis/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable aegis-api aegis-worker aegis-beat aegis-frontend
sudo systemctl start aegis-api aegis-worker aegis-beat aegis-frontend
```

Check status:

```bash
systemctl status aegis-api --no-pager
systemctl status aegis-worker --no-pager
systemctl status aegis-beat --no-pager
systemctl status aegis-frontend --no-pager
```

## 9. Configure Nginx

```bash
sudo cp /opt/aegis/deploy/nginx/aegis.conf /etc/nginx/sites-available/aegis
sudo ln -s /etc/nginx/sites-available/aegis /etc/nginx/sites-enabled/aegis
sudo rm -f /etc/nginx/sites-enabled/default
sudo nano /etc/nginx/sites-available/aegis
sudo nginx -t
sudo systemctl reload nginx
```

The included Nginx file is already set for `aegis.ctpsandbox.com` and expects
Certbot-style certificate files under `/etc/letsencrypt/live/aegis.ctpsandbox.com/`.
If your certificate is stored elsewhere, update `ssl_certificate` and
`ssl_certificate_key` before reloading Nginx.

For HTTPS, install Certbot or use your company TLS certificate process. After TLS is enabled, update `/etc/aegis/backend.env`:

```dotenv
APP_BASE_URL=https://aegis.ctpsandbox.com
CORS_ORIGINS=https://aegis.ctpsandbox.com
ALLOWED_HOSTS=aegis.ctpsandbox.com
REFRESH_COOKIE_SECURE=true
```

Then restart the API:

```bash
sudo systemctl restart aegis-api
```

## 10. Create the First Admin

Use the "First Admin" tab in the web UI with the `SETUP_TOKEN`, or seed the local default admin:

```bash
sudo -u aegis bash
cd /opt/aegis/backend
set -a
. /etc/aegis/backend.env
set +a
. .venv/bin/activate
python -m app.devtools seed
exit
```

Default seed credentials are controlled by:

- `DEV_SEED_ADMIN_EMAIL`
- `DEV_SEED_ADMIN_PASSWORD`

## 11. Verify

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
curl http://127.0.0.1:3000
curl https://aegis.ctpsandbox.com
```

Useful logs:

```bash
journalctl -u aegis-api -f
journalctl -u aegis-worker -f
journalctl -u aegis-beat -f
journalctl -u aegis-frontend -f
sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

## 12. Updating the App

```bash
sudo -u aegis bash
cd /opt/aegis
git pull

cd backend
. .venv/bin/activate
pip install -e .
set -a
. /etc/aegis/backend.env
set +a
alembic upgrade head

cd ../frontend
cp .env.production.example .env.production
npm ci
npm run build
exit

sudo systemctl restart aegis-api aegis-worker aegis-beat aegis-frontend
```

## 13. Native Local Run Without systemd

For quick checks on the VM after PostgreSQL and Redis are running:

```bash
./start.sh
```

This starts the API, worker, beat, and frontend as foreground dev processes.
