# Contributing to NTS Feed

This project aims to be easy to understand, safe to modify, and friendly to outside contributors. Contributions that improve clarity, testability, runtime correctness, and extension points are especially valuable.

## Ground Rules

- Keep user-visible behavior stable unless the change explicitly documents a breaking change.
- Prefer small, reviewable pull requests over broad mixed-purpose changes.
- Update tests and docs alongside behavior changes.
- Do not commit secrets, local caches, or generated runtime data.

## Local Setup

### From source

```bash
git clone https://github.com/avxe/nts-feed.git
cd nts-feed
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
cp env.example .env
```

Set `SECRET_KEY` in `.env` before running the app.

### Docker

The tracked development workflow is:

```bash
make setup
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Do not rely on an untracked `docker-compose.override.yml`.

## Running Checks

Run the same checks expected by CI before opening a PR:

```bash
ruff check .
.venv/bin/pytest tests/ -q
node --test tests/js/*.mjs
```

If you change packaging or install flow, also run:

```bash
python -m build --sdist --wheel
```

## Project Structure

- `nts_feed/`: application package, route handlers, services, DB, storage, and CLI modules.
- `templates/`: Jinja templates.
- `static/`: CSS and JavaScript.
- `docs/`: maintained project documentation.
- `config/scripts/`: transitional operator scripts only; supported maintenance commands should live in the package.

Prefer imports from the package namespace, for example:

- `from nts_feed.app import create_app`
- `from nts_feed.services.youtube_service import YouTubeService`
- `from nts_feed.scrape import load_shows`

## Pull Requests

- Use a descriptive branch name and commit history.
- Explain the user-visible change and the verification you ran.
- Call out any follow-up work or known limitations.
- Keep PR descriptions concrete: changed behavior, changed files, tests run.

## Reporting Problems

When opening an issue or PR, include:

- Clear reproduction steps.
- Expected and actual behavior.
- Relevant logs or screenshots.
- Local environment details when they matter.

## Security

For vulnerabilities, do not open a public issue first. Follow the process in `SECURITY.md`.
