# Django pack

A Django and DRF API on PostgreSQL with PostGIS and Redis, managed with uv. The pack holds the quality gates, the Docker test stack and example HTTP tests; the app's own code comes from the setup steps below.

## Files
| File | What it does | What it assumes |
|---|---|---|
| `kit.json` | Journeys gate: `docker compose -f compose.test.yml run --build --rm journeys`, stale when the apps, config, dependencies, image, stack or tests change. Review always for auth, permissions, payments, billing, security, migrations and settings | Docker; nothing else on the host |
| `ruff.toml` | Rule sets E W F I N UP B C4 SIM RET PIE PERF RUF C90 PL ERA BLE S T20 DJ ARG TRY FBT SLF TID PT DTZ PTH LOG, complexity 10, no docstring rules. Migrations are skipped; tests may assert and use magic values; split settings may star-import | ruff 0.16 |
| `mypy.ini` | Strict mypy with the django-stubs and DRF plugins, reading `config.settings.test`; migrations skipped | `django-stubs` matching Django's minor version |
| `.importlinter` | Example layers contract: inside each `apps.<app>`, `urls` → `views` → `serializers` → `services` → `selectors` → `models`, with missing layers skipped | `apps/` and `config/` are packages |
| `pytest.ini` | pytest-django with `config.settings.test`, tests under `apps/` | |
| `Dockerfile`, `.dockerignore` | `runtime` runs gunicorn on 8000 as a non-root user; `test` adds the dev dependencies and runs the role matrix | `pyproject.toml` and `uv.lock` at the root; GDAL from Debian |
| `compose.test.yml` | PostGIS on tmpfs, Redis, Mailpit, `migrate`, `seed`, the API with every paid key forced empty, then `role-matrix` and `journeys` (profile `tests`), and Schemathesis per role (profile `fuzz`) | See [Assumptions](#assumptions) |
| `journeys/*.hurl`, `journeys/vars.env` | Example journeys for a notes API: the happy path in the client's call order, then 400, 401, 403, 404 for another user's note, 405 and 409, never a 5xx | `base_url` is `http://localhost:8000` |
| `tests/matrix/roles.yaml`, `tests/matrix/test_role_matrix.py` | The role matrix, in the same format as the MERN pack's, and a pytest runner over real HTTP (stdlib `http.client` and PyYAML). A fresh fixture per cell | The admin login matches `x-admin` in `compose.test.yml` |
| `tests/schemathesis/*.toml` | Schemathesis configs, one per role: anonymous, and the admin through a JWT login | drf-spectacular's `jwtAuth` scheme for simplejwt |
| `.lefthook/django.yml` | Pre-commit: `ruff check` and `ruff format --check` on staged Python files, skipped until `uv.lock` exists | The common `lefthook.yml` extends it |
| `.github/workflows/ci.yml` | Lint, format, mypy, import-linter, `makemigrations --check --dry-run`, pytest against PostGIS and Redis services, `check --deploy --fail-level WARNING` with production settings, a stale-schema check, `oasdiff breaking` on pull requests, the journeys, Betterleaks over the history and osv-scanner | Actions pinned by SHA; Betterleaks by version and checksum |
| `.env.example` | The variables the settings read, secrets empty | |
| `CLAUDE.md.template` | The stack section of CLAUDE.md: layout, settings split, layers, and the run and check commands | |

### Assumptions
- Python 3.13, uv 0.12, Django 5.2 LTS. Django 6.x isn't declared supported yet by drf-spectacular, simplejwt, django-environ or django-cors-headers; move up when they are.
- `manage.py` at the root, settings in `config/settings/{base,local,production,test}.py`, apps in `apps/<app>/` with `apps/__init__.py`.
- The API listens on 8000 and serves `GET /api/health/`, `GET /api/schema/` (drf-spectacular), `POST /api/v1/auth/signup/` (201) and `POST /api/v1/auth/token/` (simplejwt, answering `access` and `refresh`).
- Errors are `application/problem+json` with `status` and an `errors` list of `{code, detail, attr}`: drf-standardized-errors with a problem+json renderer.
- `python manage.py seed_test_accounts` creates or updates the admin from `ADMIN_EMAIL` and `ADMIN_PASSWORD`, prints nothing secret, and can run twice.
- The settings read `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, `REDIS_URL`, `EMAIL_URL` and the CORS and CSRF origins through django-environ.

## Setup for a new project
Run these after the bootstrap lays the pack down.

1. Create the project and add the dependencies:
   ```sh
   uv init --bare --python 3.13
   uv add 'django~=5.2.17' djangorestframework drf-spectacular djangorestframework-simplejwt django-environ \
     'psycopg[binary]' gunicorn redis django-cors-headers drf-standardized-errors
   uv add --dev ruff 'mypy~=1.19' 'django-stubs[compatible-mypy]~=5.2.9' 'djangorestframework-stubs~=3.16.9' \
     types-PyYAML import-linter pytest pytest-django pyyaml
   ```
   The stubs follow Django's minor version: django-stubs 5.2 with djangorestframework-stubs 3.16 and mypy below 1.20.
2. `uv run django-admin startproject config .`, then split `config/settings.py` into the four settings files, and `mkdir apps && touch apps/__init__.py`. New apps: `mkdir apps/<app> && uv run python manage.py startapp <app> apps/<app>`, with `name = "apps.<app>"` in its `AppConfig`.
3. Write the health and schema endpoints, auth, the error handler and `seed_test_accounts`, following `~/.claude/rules/django.md` and `api-design.md`.
4. Replace the example journeys, matrix and Schemathesis roles with the app's own, in the client's call order.
5. Generate and commit the schema: `uv run python manage.py spectacular --file docs/api/openapi.yaml --validate`.
6. `lefthook install`, then record the type baseline with the kit's `kit_baseline.py`.

## Settings split
- `base.py` reads every value through django-environ and gives required ones no default, so a missing `SECRET_KEY` stops startup with `ImproperlyConfigured`. `DEBUG` defaults to False.
- `local.py` sets dev defaults, `production.py` sets none and passes `check --deploy --fail-level WARNING`.
- `test.py` sets throwaway values (so pytest and mypy run without a `.env`) and refuses to start while a paid key is set:
  ```python
  PAID_API_KEYS = ("OPENAI_API_KEY", "GOOGLE_MAPS_API_KEY", "TWILIO_AUTH_TOKEN")
  if any(env.str(name, default="") for name in PAID_API_KEYS):
      raise ImproperlyConfigured("Test settings run with paid API keys empty. Unset: " + ", ".join(PAID_API_KEYS))
  ```

## Checks
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv run lint-imports`, `uv run pytest`.
- HTTP tests: `docker compose -f compose.test.yml run --build --rm journeys`. It builds the images, starts the stack, runs the role matrix, then the journeys, and fails if either fails. Read `docker compose -f compose.test.yml logs role-matrix` when the matrix fails.
- Schemathesis: `docker compose -f compose.test.yml run --rm schemathesis-admin`. Record accepted failures once with `--baseline`; the list only shrinks.
- Workflow: `actionlint .github/workflows/ci.yml`.

The tests run in the API container's network for the same reasons as the MERN pack: a single `docker` command works inside Claude Code's sandbox, and the host needs no Hurl or Python test dependencies.

## How it was checked
On a scratch Django project with one app (models, selectors, services, serializers, views, urls, a migration): ruff 0.16.10 loaded `ruff.toml` and passed the sample, skipping the migration; mypy 1.19.1 with django-stubs 5.2.9 and djangorestframework-stubs 3.16.9 (and mypy 2.3.1 with the 6.1 stubs) loaded the plugins and reported the strict errors planted in a view; import-linter 2.15 kept the contract, then broke it on a model importing a view; pytest-django 4.14 ran with `pytest.ini`. The role-matrix runner passed ruff and strict mypy, and ran 28 cells against a stand-in HTTP server, failing on a wrong expectation. Schemathesis 4.28.0 loaded both role configs and rejected a misspelled key. hurlfmt 8.0.1 parsed the journeys. `docker compose config` validated the stack, and actionlint 1.7.12 the workflow.

Not checked: the images building, the stack running and the journeys passing against a real Django app; the workflow on GitHub; the oasdiff action reading git refs inside its container.
