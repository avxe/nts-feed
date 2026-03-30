# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting a Vulnerability

Do not open a public issue for an undisclosed security problem.

Report vulnerabilities privately to the maintainer contact listed in the repository profile or project metadata. Include:

- A clear description of the issue.
- Reproduction steps.
- Impact and affected components.
- Any proposed mitigation if you already have one.

The target response time is 48 hours for an initial acknowledgement.

## Repository Hygiene Requirements

- Never commit `.env`, token caches, runtime JSON, downloads, or local editor state.
- Run the configured secret scan before opening a PR.
- Treat any committed credential as compromised: rotate it and remove it from git history.

## Deployment Guidance

- Set a strong `SECRET_KEY`.
- Keep `ENABLE_TALISMAN=true` unless you are deliberately running a local insecure test environment.
- Set `FORCE_HTTPS=true` behind a real HTTPS reverse proxy in production.
- Keep Docker images and Python dependencies current.
- Restrict filesystem permissions around SQLite data, downloads, and cached assets.

## Security-Sensitive Areas

- External HTTP requests to NTS, Discogs, Last.fm, YouTube, SoundCloud, and Mixcloud.
- Download and metadata-tagging flows that handle third-party media.
- Admin settings endpoints and background maintenance flows.
- Locally stored listening history, playlists, and downloaded media.

## Maintainer Checklist for Secret Exposure

If a secret is ever committed:

1. Rotate the secret immediately.
2. Remove the file or value from the working tree.
3. Scrub the secret from git history.
4. Update scanning rules or ignore patterns so the same class of leak is blocked in the future.
