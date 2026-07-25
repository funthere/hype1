# Security Policy

## Supported operation

Only **paper trading** and supervised **testnet** workflows are supported. All
mainnet launchers fail closed while execution lifecycle release gates remain
incomplete. Never use a production private key in tests, CI, logs, issues, or
sample configuration.

## Reporting a vulnerability

Do not publish exploitable details or credentials in a public issue. Report
security concerns privately to the repository maintainers, including impact,
reproduction steps, affected version/commit, and any safe proof of concept.
Maintain access controls while a fix is prepared and coordinate disclosure with
the maintainer.

## Credential response

If a private key, API wallet, or Telegram token is exposed:

1. Stop affected bot processes and cancel exchange orders.
2. Revoke or rotate the key at the provider immediately.
3. Transfer remaining funds only after independently confirming exchange state.
4. Preserve the SQLite database and logs as incident evidence; do not edit them.
5. Remove the secret from source/history according to the hosting provider's
   incident procedure and use a newly generated credential.

## Dependency updates

Dependency changes require a reviewed `requirements.in` update, regenerated
hash-pinned lockfile, vulnerability scan, and the full deterministic test suite.
