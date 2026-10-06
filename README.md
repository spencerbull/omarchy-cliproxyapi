# CLIProxyAPI for Omarchy

Your signed-in AI accounts, at a glance. A native Omarchy bar plugin with per-account activity, request counts, cooldowns, and remaining usage limits.

![Account activity with synthetic demonstration data](docs/overview.png)

*Native QML, synthetic accounts. Colors, typography, and controls follow your Omarchy theme.*

## Install

Requires Omarchy with the Quickshell plugin system and Python 3. No Python packages or build step.

```sh
omarchy plugin add https://github.com/spencerbull/omarchy-cliproxyapi --enable
```

Click the server icon in the bar, enter your **server URL** and **management key**, and select **Connect**. Use the server's management key, not an inference API key.

- Remote: `https://proxy.example.com` with a valid TLS certificate and remote management enabled.
- Local: `http://127.0.0.1:8317`.
- Reverse-proxy prefixes, `/v0/management`, and `/management.html` URLs are accepted.

The plugin refreshes activity every minute. Refresh manually from the panel or middle-click the bar icon. Authentication failures stop polling; reconnect in Settings after correcting the key.

## Accounts first

Each row shows the account email when reported, provider, plan, readiness or cooldown, recorded request/attempt count, and last activity. The activity strip shows recent volume with failures highlighted. Missing metrics are shown as unavailable, never silently converted to zero.

Filter by provider, search accounts, or sort by recent activity and request volume. The eye button hides account identities and clears the search. Expand an account for success/failure totals, attributed tokens and models when available, retry time, and usage limits.

**Check limits** reads Codex or Claude allowance only when requested. Segmented bars show the percentage **remaining**, with reset countdowns and the time of the check. Limits are a cached snapshot; refresh to update them. Other providers display an explicit unsupported state.

![Account usage limits with synthetic demonstration data](docs/limits.png)

## What the numbers mean

| Data | Behavior |
| --- | --- |
| Modern account counters | Upstream attempts, including retries and fallbacks; may exceed client request counts |
| Recent activity | Server-reported ten-minute windows, labeled approximate and displayed in server time |
| Exact last request | Available only from legacy request details uniquely matched to the account's `auth_index` |
| Tokens and models | Shown only when request history can be attributed to that account |
| No timestamp | “Not reported”; file modification and credential refresh dates are never substituted |
| Account limits | Explicit Codex/Claude quota reads; a failed check does not disconnect the activity monitor |

Counters are server-reported and are not a durable billing ledger. A modern activity window may be newer than an available exact legacy timestamp; the newer window takes precedence. Account identities do not identify individual agent processes. The plugin cannot determine which running agent made a request from these counters.

The read endpoints are `/v0/management/auth-files`, optional legacy `/usage`, and optional `/api-key-usage`. The legacy usage snapshot was removed in newer upstream versions. Quota checks use management `POST /api-call` to perform fixed provider **GET** requests, with provider tokens substituted on the server. The plugin never receives those tokens.

**The plugin never consumes `/usage-queue`, downloads credential files, changes routing, resets limits, refreshes credentials, or issues inference requests.** Muse/Meta and xAI quota checks are unsupported because their dashboard flows require credential access or may issue inference requests.

## Credentials and privacy

By default the URL and management key stay in memory for the shell session. The key travels to the helper through stdin, never command arguments or environment variables; the password field clears after submission. It is never written to `shell.json` or the plugin checkout.

Optional **Remember on this device** saves plaintext credentials outside the plugin:

```text
${XDG_CONFIG_HOME:-~/.config}/omarchy-cliproxyapi/credentials.json
```

The directory is mode `0700`, the file is `0600`, and writes are atomic. **Forget connection** removes this file and clears the dashboard. Uninstalling does not remove it; forget the connection first.

HTTPS verification stays enabled. HTTP is allowed only on loopback. Redirects and environment HTTP proxies are disabled. Responses are bounded and errors use fixed messages. Only selected display fields reach QML: explicit account emails are retained for local identification; raw API keys, credential filenames, tokens, and upstream error payloads are discarded. Unknown provider names become `custom`.

Omarchy plugins run as your desktop user. File permissions cannot protect saved keys from other processes running as that user. No telemetry or network requests occur before a connection is configured.

## Development

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_display.cjs
omarchy plugin validate .
python3 scripts/preview.py
```

The preview runs the real QML and helper against a loopback-only synthetic API, with an isolated configuration directory. Its printed demo URL and key are fictional. Set `OMARCHY_PATH` if the shell modules are not found automatically.

Tests cover account attribution, exact versus approximate activity, quota parsing, credential handling, HTTP boundaries, and display behavior. Examples and screenshots use synthetic data. Never commit real server responses, account screenshots, credentials, `.env` files, or local configuration.

Compatibility references: upstream [legacy usage](https://github.com/router-for-me/CLIProxyAPI/blob/v6.9.49/internal/api/handlers/management/usage.go), [v8.0.16 account inventory](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.16/internal/api/handlers/management/auth_files.go), and [API-key counters](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.16/internal/api/handlers/management/api_key_usage.go).

MIT licensed. An independent community plugin; not affiliated with CLIProxyAPI or Omarchy.
