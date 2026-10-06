# CLIProxyAPI for Omarchy

A native Omarchy bar plugin for your AI proxy. See request volume, token and model usage, and the provider connections behind your agents without leaving the desktop.

## Install

Requires an Omarchy release with the Quickshell plugin system and Python 3. No Python packages, build step, or browser dashboard are required.

```sh
omarchy plugin add https://github.com/spencerbull/omarchy-cliproxyapi --enable
```

Click the robot icon in the bar. Enter your **server URL** and **management key**, then select **Connect**. Use the management key configured on the server, not an inference/client API key.

- `https://proxy.example.com` — a remote server with a valid TLS certificate.
- `http://127.0.0.1:8317` — a local server.
- Reverse-proxy path prefixes, `/v0/management`, and `/management.html` URLs are supported.
- Remote servers must permit remote management in CLIProxyAPI. This plugin does not change server configuration.

The panel refreshes every minute. Use **Refresh** or middle-click the bar icon to refresh immediately. Authentication failures stop automatic retries to avoid the server's failed-login ban. Reconnect with the correct key in Settings.

## What it shows

| View | Contents |
| --- | --- |
| Overview | Requests or upstream attempts, success rate, activity history, and token/model/client totals when available |
| Connections | Provider, anonymous connection label, reported status, and successful/failed attempts |
| Settings | Server URL, masked management key, optional local persistence, and forget connection |

The plugin uses Omarchy's live colors, typography, controls, and popup placement. Lists scroll; Tab navigates controls and Escape closes the panel.

### API compatibility and attribution

CLIProxyAPI versions before v6.10 expose `GET /v0/management/usage`. On those servers, the dashboard shows reported request/token totals, model usage, anonymous client-key groups, and daily activity. Enable usage statistics on the server if needed; the plugin does not enable them for you.

Newer versions removed that snapshot API. The plugin uses the read-only `auth-files` and `api-key-usage` counters instead and explicitly marks token/model totals unavailable. These are **upstream attempts**, so retries and fallbacks can produce more attempts than client requests. API-key connections do not expose a health state; their status is shown as unknown. Counters are server-reported, not a durable billing ledger. Legacy daily charts show the latest reported dates, not a rolling 24-hour window; recent connection charts use server-local time.

Client groups correspond to API keys, not running agent processes. To distinguish agent traffic on a server with legacy usage statistics, give each agent a separate client API key. Shared keys cannot identify individual agents. Connection labels are anonymous and may change when the server's inventory changes.

**The plugin never polls `/usage-queue`.** That endpoint consumes records and could interfere with another collector. It does not fetch provider credentials, change routing, create API keys, or start agent sessions.

## Credentials and privacy

By default, the URL and management key stay in the helper's memory for the shell session. The key is passed through a private stdin pipe, never command arguments or environment variables. The password field clears after submission. It is never written to `shell.json` or the plugin checkout.

If you explicitly select **Remember on this device**, credentials are saved as plaintext at:

```text
${XDG_CONFIG_HOME:-~/.config}/omarchy-cliproxyapi/credentials.json
```

The directory is mode `0700`, the file is mode `0600`, and writes are atomic. **Forget connection** removes the saved credentials and clears the dashboard. Uninstalling the plugin does not remove this separate file; forget the connection before uninstalling, or remove that exact file yourself.

HTTPS certificate verification is always enabled. Plain HTTP is accepted only for localhost/literal loopback addresses. Redirects and environment HTTP proxies are disabled. Server responses are bounded and errors are replaced with fixed messages. Only allowlisted fields cross from Python into QML: raw API keys, emails, account names, credential filenames, and error payloads are discarded. Unknown/custom providers appear as `custom`.

Omarchy plugins run as your desktop user, not in a security sandbox. File permissions do not protect saved keys against other processes running as your user. This plugin sends no telemetry and makes no network requests before you configure a connection.

## Development

```sh
python3 -m unittest discover -s tests -v
omarchy plugin validate .
```

Tests use only synthetic credentials and local HTTP fixtures. Do not add real server responses, screenshots of private accounts, credentials, `.env` files, or local configuration to Git.

The management API compatibility contract was checked against upstream [v6.9.49 usage](https://github.com/router-for-me/CLIProxyAPI/blob/v6.9.49/internal/api/handlers/management/usage.go), [v8.0.16 auth files](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.16/internal/api/handlers/management/auth_files.go), and [v8.0.16 API-key counters](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.16/internal/api/handlers/management/api_key_usage.go).

MIT licensed. An independent community plugin; not affiliated with CLIProxyAPI or Omarchy.
