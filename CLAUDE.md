# janus — Claude instructions

## Start of every session

1. Read `docs/architecture.md` — it is the authoritative module/schema/flow reference.
2. If the user mentions a specific feature or ticket, read the relevant file in `docs/tickets/` before looking at source code.

## Running tests

```bash
just test
```

This runs pytest inside a Docker container via `ghcr.io/astral-sh/uv:alpine3.22`. Never
run pytest directly with system Python or the `.venv` — dependencies are only guaranteed
inside the container.

## Ticket workflow

Tickets live in `docs/tickets/NNN-slug.md`. Use `/new-ticket` to create one interactively.

- Status values: `open` | `in-progress` | `done`
- Number tickets sequentially (pad to 3 digits: `001`, `002`, …)
- Keep the file after implementation — update status to `done`

When asked to implement a ticket:
1. Read `docs/architecture.md`
2. Read the ticket file
3. Read any tickets listed in the ticket's **Related tickets** section for additional context
4. Read only the source files the ticket touches — avoid broad exploration
5. Implement and test
6. Review the code, specifically with a focus on security
7. Review the code for GDPR relevancy and prompt the user for potentially needed changes
8. Update `docs/architecture.md` to reflect any new modules, routes, or background tasks introduced
9. Do a final review, check that the test coverage is good and commit

## Commit style

- Imperative subject line, present tense ("Add …", "Fix …", "Remove …")
- Body explains *why*, not *what*
- Always add `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`

## Code conventions

- No comments unless the *why* is non-obvious
- No docstrings
- Synchronous HTTP via `requests` (not httpx/aiohttp)
- New background tasks: add an `asyncio.Task` to `main.py` lifespan; run blocking work
  via `run_in_executor(None, fn)` to avoid stalling the event loop — follow `_scheduled_sync`
- New routes: add handler to `main.py`; authenticate with `hmac.compare_digest` against `API_KEY`
- No database — janus has no persistent storage; state is held in module-level cache
- Tests: patch all HTTP at `main.pocket.*` / `main.outline.*` / `main.ssh.*`; use
  `TestClient(main.app)` for route tests; reset `main.pocket_userstore` and
  `main.last_updated_timestamp` in `setup_method` for cache-sensitive tests
