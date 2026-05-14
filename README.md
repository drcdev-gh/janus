# janus — SSO permission propagation daemon

Lets other services commonly found in organisations or homelabs access group or claim information from PocketID.

## Outline

- Syncs all users and groups from PocketID to Outline
- Extra groups in Outline are deleted
- Disabled users in PocketID are suspended in Outline and reactivated if re-enabled

## SSH

Lets users validate their SSH key in order to gain access via `AuthorizedKeyCommand`.
A passed in SSH key is valid if it is stored as a custom claim `ssh-pubkey` on the user
and if the user is part of the configured `SSH_ALLOWED_GROUP` environment variable.

Here's an example of how to call the endpoint:
```
curl -s -G --data-urlencode "pubkey=ssh-ed25519 mykey" -H "x-api-key:mysecretkey" http://127.0.0.1:8085/ssh/validate
```

To use this in the SSH config, use the included script `verify_key.sh` like so:
```
Match User oidc
    AuthorizedKeysFile none
    AuthorizedKeysCommand /usr/local/bin/verify_key.sh %t %k
    AuthorizedKeysCommandUser oidc
```

Make sure to modify the hostname and API key in the script.

