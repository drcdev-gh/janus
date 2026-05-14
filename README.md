# janus — SSO permission propagation daemon

Syncs user groups and claims from [PocketID](https://github.com/pocket-id/pocket-id)
to other services. Currently supports [Outline](https://www.getoutline.com/) and SSH key
validation.

## Features

### Outline sync

- Groups in PocketID are created in Outline if missing, deleted if removed
- Group memberships are kept in sync per user
- Users disabled in PocketID are suspended in Outline and reactivated if re-enabled
- Syncs automatically every 30 minutes in the background
- Can also be triggered on demand via `GET /outline/sync`

### SSH key validation

Validates SSH public keys against PocketID custom claims, suitable for use as an
`AuthorizedKeysCommand` backend. A key is accepted if:

1. The user has a `ssh-pubkey` custom claim in PocketID matching the presented key
2. The user is a member of the group named by `SSH_ALLOWED_GROUP`

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```sh
cp .env.example .env
```

| Variable | Description |
|---|---|
| `POCKETID_API_URL` | Base URL of your PocketID instance (must be `https://`) |
| `POCKETID_API_KEY` | PocketID admin API key |
| `OUTLINE_API_URL` | Base URL of your Outline instance (must be `https://`) |
| `OUTLINE_API_KEY` | Outline API token — see required permissions below |
| `SSH_ALLOWED_GROUP` | PocketID group whose members may log in via SSH |
| `API_KEY` | Shared secret for authenticating requests to Janus |

**Required Outline API token permissions:**
`groups.create` `groups.list` `groups.delete` `groups.memberships`
`groups.add_user` `groups.remove_user`
`users.list` `users.suspend` `users.activate`

## Running

```sh
just run-dev   # build and start with Docker Compose (uses .env)
just run-tag 1.2.3  # run a specific published image tag
```

## SSH setup

Add to your `sshd_config`:

```
Match User oidc
    AuthorizedKeysFile none
    AuthorizedKeysCommand /usr/local/bin/verify_key.sh %t %k
    AuthorizedKeysCommandUser nobody
```

Install `verify_key.sh` (from `test/ssh/`) on the SSH host and update the
hostname and API key inside it to point at your Janus instance.

To test the endpoint directly:

```sh
curl -s -G \
  --data-urlencode "pubkey=ssh-ed25519 AAAA..." \
  -H "x-api-key: your-api-key" \
  https://janus.example.com/ssh/validate
```

A matched key is returned as plain text with a trailing newline. An unmatched or
invalid key returns HTTP 204 with an empty body.

## Development

**Run the tests:**

```sh
just test
```

Tests use `unittest.mock` to patch all HTTP calls — no live PocketID or Outline
instance required.

**Project layout:**

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, caching, scheduled sync, route handlers |
| `pocket.py` | PocketID API client |
| `outline.py` | Outline API client and sync logic |
| `ssh.py` | SSH key format validation |
| `tests/` | pytest suite |
| `test/ssh/` | Docker-based SSH server for integration testing |
