# Private KRX source for the monitoring relay

The monitoring repository may consume only the designated files under
`hanull01/KIS_Trading:data/krx_sync`. It must use an authenticated read-only
GitHub Contents client with repository-level `contents:read` access. GitHub
does not make that permission path-scoped; the adapter's allowlist is the
path-level control. It must not clone the repository, read KIS source code,
read Actions, or write to the private repository.

The source adapter fetches exactly:

- `CURRENT`
- `generation_manifest.json`
- `monitoring_interchange.json`
- `private_sync_manifest.json`
- `report_input.json`
- `transfer_manifest.json`

It validates the generation/date, M10 schema, entity count and identity
uniqueness, allowlisted artifact hashes and sizes, and the KRX historical
priority policy. Raw KRX files, the internal metrics bundle, credentials, and
after-market observations are rejected. NAVER current/after-market data stays
outside this historical input.

The adapter has no GitHub network implementation or secret access. The caller
must inject an approved `contents:read` client. A production GitHub App or
token must have read access only to `hanull01/KIS_Trading`; it must not receive
`contents:write`, Actions, workflow, or KIS runtime permissions. No secret is
added by this change.
