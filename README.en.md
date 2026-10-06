# CodexPlusPlus-Mac

An independent macOS CLI by tanrich for using the official Codex browser extension while retaining API-key login. Inspired by [Codex++](https://github.com/BigPizzaV3/CodexPlusPlus); its small identification helper is preserved unchanged. This project is not an official upstream port or an OpenAI product, and it does not port the full Codex++ application.

## Requirements

- macOS Apple Silicon, Python 3.9+ (standard library only).
- Verified CUA runtime: `0.0.16/20260915001755-492f19756c31`, Node `24.21.0`.
- Codex/ChatGPT App running, official Edge extension installed and enabled.
- Other App/runtime versions are rejected when file hashes do not match. Chrome is covered by offline helper tests but has not been tested live.

## Install

```sh
git clone https://github.com/tanrich/CodexPlusPlus-Mac.git
cd CodexPlusPlus-Mac
python3 manage-adapter.py check
python3 manage-adapter.py install
python3 ~/.codex/codexplusplus-mac/bin/manage-adapter.py alias
```

Open a new terminal:

```sh
manage-adapter enable
manage-adapter status
```

`install` creates a disabled external service and a private restoration backup under `~/.codex/codexplusplus-mac/`. `enable` then switches only the cached plugin's browser mapping and the local control switch. The installed command no longer needs the cloned repository. `alias` backs up `.zshrc` and refuses to replace a different existing `manage-adapter` alias.

Existing adapters must be restored using their own management tool before installing this one. No previous installation or alias is migrated automatically. If several plugin caches exist, use `--descriptor` to select the App's active `.mcp.json`; use `--app` for a nonstandard App location. `--codex-home` and `CODEX_HOME` select a custom Codex user directory.

## After a restart

Open Edge and the App, run `manage-adapter enable`, then create a new task and ask it to reinitialize browser tools and read the number of tabs through the Edge extension. A successful live tab query verifies connectivity. The CLI only reports local configuration; it does not reload existing tool connections. The App may regenerate the cached mapping after a restart or plugin sync.

## Commands

| Command | Purpose |
| --- | --- |
| `check` | Verify the installed App against the supported runtime hashes |
| `install` | Prepare a disabled external service and install this CLI |
| `alias` | Add the installed command to zsh |
| `status` | Display readable Chinese configuration and validation status |
| `enable` | Verify files, enable the mapping and local switch; repeatable |
| `restore` | Restore the official browser mapping and disable the local switch |
| `status --json` | Emit machine-readable output |

No action defaults to `status`. `restore` does not change login. Use it when disabling or diagnosing the adapter, or after switching to ChatGPT login. Restoring while remaining in API-key mode may bring back the original authentication error.

## Evidence and limitations

The local predecessor was tested on 2026-10-06 with the stated arm64 runtime. A fresh official CUA connection enumerated Edge tabs and successfully read a page, clicked a counter, and entered and read back Chinese text. The portable release uses the same helper/callback and is covered by synthetic installation and restoration tests; it is not a claim of live validation on every installation.

A local header-check navigation returned `net::ERR_BLOCKED_BY_CLIENT`; no actual header value was confirmed. Closing a test tab produced header-preparation errors before the tab was eventually confirmed absent. Full stability, user approval/refusal, stopping, extension-state cleanup and persistence across restarts are not fully verified.

Controlled requests may carry `x-browser-agent: ChatGPT/<session-id>`. This is browser-agent identification, not an authentication token. The extension may retain the identification setting; `restore` does not clear it. The adapter does not modify login files, the signed App, the extension, site approvals or trusted code roots. It does not impersonate an authenticated ChatGPT account.

The App service is read and overlaid locally. Proprietary App service code and generated backups are excluded from this repository and its releases. Upstream Codex++ already lists macOS packages; this repository focuses on the browser adapter CLI.

## Tests and license

```sh
python3 -m unittest discover -s tests -v
node --experimental-vm-modules --test tests/helper.test.mjs
```

Tests use temporary synthetic files and do not require login credentials. Helper tests require Node.js 22+. License: **AGPL-3.0-only**; Copyright (C) 2026 tanrich for original work, Copyright (C) 2026 BigPizzaV3 for the upstream helper. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
