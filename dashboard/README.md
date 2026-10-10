# Tripwire dashboard

A local web dashboard over the screener's weekly runs. It reads `data/history/screen_YYYY-MM-DD.csv` (the full scored frame of every run) and `config.toml`. Its one write path is the parameter editor on the Method view, which edits `config.toml` in place. It never fetches market data and needs no extra dependencies: Python's standard-library HTTP server, pandas (already required by the screener) and plain HTML/CSS/SVG with the fonts bundled.

## Run

```powershell
run_dashboard.bat                                  # opens http://127.0.0.1:8765
.venv\Scripts\python -m dashboard --port 8770      # another port
.venv\Scripts\python -m dashboard --root D:\other-checkout   # read runs from another checkout
.venv\Scripts\python -m dashboard --read-only          # no parameter editing at all
```

After a new screener run, press **Reload** in the top bar. No restart is needed, because run files are re-read when their timestamp changes.

## Access from other devices (Tailscale, no domain needed)

The dashboard has no login, and the run data includes your holdings. Keep it on loopback and let Tailscale Serve publish it to your own tailnet only. Parameter edits are accepted only from the machine itself; other tailnet devices see the Method view read-only unless you start with `--allow-remote-edit`. Your devices then reach it over HTTPS at `https://<laptop>.<tailnet>.ts.net`. **Do not use `tailscale funnel`**, because that makes it public.

```powershell
# 1. this laptop's tailnet name, e.g. my-laptop.tail1234.ts.net
(tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')

# 2. publish on its own HTTPS port (443 stays free for anything else)
tailscale serve --bg --https=8443 http://127.0.0.1:8765

# 3. run the dashboard and accept that host name (the Host header check blocks unknown names)
.venv\Scripts\python -m dashboard --allow-host my-laptop.tail1234.ts.net
```

Then open `https://my-laptop.tail1234.ts.net:8443/` on your phone or another machine signed in to the same tailnet. To share port 443 with something else, mount it under a path instead: `tailscale serve --bg --set-path /screener http://127.0.0.1:8765` plus `--base-path /screener`. To remove the mount, run `tailscale serve --https=8443 off`.

To start the dashboard at logon, without a console window, run this once from the repository root in PowerShell.

```powershell
$a = New-ScheduledTaskAction -Execute "$PWD\.venv\Scripts\pythonw.exe" -Argument "-m dashboard --allow-host my-laptop.tail1234.ts.net" -WorkingDirectory $PWD
Register-ScheduledTask -TaskName "Tripwire dashboard" -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn)
```

The laptop has to be awake and online for remote access.

## Views

| View | What it answers |
|---|---|
| Overview | How many candidates are there this week, where does every scored name sit relative to its max entry price, what is close to the line, and how does the growth the price needs compare with estimated growth? |
| Screen | The full universe with filters (status, class, source list, holdings, search), sortable column sets (decision, valuation inputs, fundamentals, peer context, data) and CSV export of the current view. |
| Holdings | Euro value of holdings by status, the value-weighted entry score, each position's distance to its ceiling, and **Your entries**: the score at your average buy price next to today's score (needs `cost_eur` in the holdings file). |
| Changes | Run history, plus status events, the biggest score moves and their driver (price or ceiling) against any earlier run. |
| Data quality | Not-rankable reasons, critical flags, flag frequency, stale statements, currencies missing from the rates table, and rates older than 90 days for currencies in use. |
| Method | Definitions, structural settings, and the **parameter editor**: investor choices (equity risk premium, adverse floor spread, minimum hurdle, adverse growth mode, caps, stale threshold, base currency) and the per-currency rates table (10-year yield, terminal growth, country risk, as-of date) with hurdle and floor derived live. Review validates with the screener's own checks and lists every change; Save keeps `config.toml.bak` and a dated copy under `data/config-history/`. It warns when `config.toml` changed after the run. |

Clicking any name opens the inspector. It shows the price against the reasonable value, the adverse value and the max entry price, the valuation inputs, fundamentals, peer percentiles, flags, and price versus ceiling across runs. The selected view and name are kept in the URL hash, so a link such as `#screen?s=ADBE` reopens it.

## Conventions

- Prices, values and the max entry price are per share in the quote currency. Positions and market caps are in euro. Rates are in percent, and spreads are in percentage points (pp).
- **To ceiling** is `max entry / price − 1`: the price move that would put a name exactly on its max entry price. Use it for the exact gap where the entry score is floored at −100.
- Status colours (green, amber, red, grey) mean status only. Chart hues were checked for colour-blind separation in both themes.
- Security: this is a local, single-user tool on Python's standard-library HTTP server, not an internet-facing service, and it has no login. It binds to 127.0.0.1 and refuses any other address unless you pass `--unsafe-bind` (anyone who can reach it could then read your data); to use it from other devices, keep it on loopback behind a private proxy such as Tailscale Serve. It rejects non-loopback `Host` headers (DNS rebinding), confines static files to `dashboard/static`, and sends a strict Content-Security-Policy. Names and flags from the data are inserted as text, never as HTML. The only write, `POST /api/config`, needs the per-process CSRF token handed out with the page, rejects cross-site `Origin` headers, is limited to this machine by default (loopback socket and Host, and none of the headers a reverse proxy such as Tailscale Serve or cloudflared adds), refuses a save if `config.toml` changed since the page loaded it, validates every value (ranges, then `screener.checks`), and replaces the file atomically after writing the backups. `--read-only` removes it.
- What stays out of the editor: structural model settings (horizon, smoothing switches, cyclical rules, the rate-over-growth margin) and everything hard-coded in the screener. Those are model changes: edit `config.toml` or the code and update the golden test deliberately.
- Scores do not change on save. Rerun the screener, then press Reload.

## Tests

```powershell
.venv\Scripts\python -m pytest -q tests/test_dashboard.py
```

## Valuation evidence

The inspector exposes the model, evidence basis, firm/equity yield claim, implied-growth solution status/roots and quote date. The Method view uses the same definitions as the generated report. Currency-rate previews exclude issuer country exposure, and operating stress uses the base discount rate. Sector assumptions are edited in `overrides/valuation.toml` (or its local copy), with the contracts in `docs/valuation-method.md`. The FX-parity display is a scenario, not an expected EUR equity return. Older saved runs retain their original outcomes and should not be interpreted as results of the revised model.
