# Repository Guidelines

## Project Structure & Module Organization
- `src/` contains the bot code (entry: `python -m src.main`).
- `config/sources.yaml` lists RSS and Reddit sources.
- `data/` stores the SQLite DB and downloaded media (videos/images).
- `docker/` holds `Dockerfile` and `docker-compose.yml` for deployment.

## Build, Test, and Development Commands
- Local setup (venv required): `python -m venv venv && source venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Run bot: `python -m src.main`
- Docker run: `docker compose -f docker/docker-compose.yml up -d`
- Docker logs: `docker compose -f docker/docker-compose.yml logs -f`
- Stop Docker: `docker compose -f docker/docker-compose.yml down`

## Coding Style & Naming Conventions
- Python 3.11+, async I/O throughout; keep functions typed.
- Indentation: 4 spaces, double quotes.
- Naming: `snake_case` for functions, `PascalCase` for classes, `UPPER_CASE` for constants.
- Error handling: log and return safe defaults instead of raising in async handlers.
- Linting: `ruff check .` (enforced in CI). Config in `ruff.toml`.
- Optional: `mypy` for type checks (not configured).

## Testing Guidelines
- Run tests: `pytest tests/ -v`
- Place test files in `tests/` named `test_*.py`.
- Tests are enforced in CI (auto-pr-merge workflow).

## Commit & Pull Request Guidelines
- Commits in history use short, imperative subjects (e.g., "Fix ...", "Add ...")
- PRs should include: purpose, key changes, how to run/verify, and any config changes.
- Never push directly to `main` — create a feature branch; the auto-pr-merge workflow handles review and merge.
- Run `ruff check .` and `pytest tests/ -v` locally before pushing.

## Branch Workflow
1. Create a feature branch: `git checkout -b fix/my-change`
2. Make changes, commit, push: `git push -u origin fix/my-change`
3. The auto-pr-merge workflow automatically creates a PR, runs CI, and squash-merges if approved.
4. After merge, clean up locally:
   ```
   git checkout main
   git pull
   git branch -D <branch-name>
   ```
   Use `-D` (uppercase) because squash-merges aren't recognized by `git branch -d`.

## Security & Configuration Tips
- Do not commit `.env`; use `.env.example` as the template.
- Required env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_ADMIN_ID`, `GEMINI_API_KEY`.
- Keep `data/` artifacts out of version control unless explicitly requested.
