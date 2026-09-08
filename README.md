# AniList to Notion Synchronization

This repository contains an automated Python workflow that synchronizes anime watchlist data from AniList to a custom Notion database. 

## AniList GraphQL Retrieval & Cloudflare Bypass

AniList's GraphQL API is protected by aggressive Cloudflare Turnstile bot mitigation, which actively blocks standard HTTP requests (like those from Python's `requests` library) via 403 Forbidden errors. 

To ensure reliable data retrieval, this script utilizes a browser-level execution strategy:
* **Playwright Automation:** The script launches a visible (headed) Chromium browser session to mimic a genuine user environment.
* **Session Trust:** It navigates to the AniList root domain and waits for the DOM to load, passively clearing the background JavaScript challenges.
* **In-Page Fetch:** Instead of executing the API call from Python, Playwright uses `page.evaluate()` to inject and run a native JavaScript `fetch()` request directly inside the cleared browser tab. This allows the GraphQL query to inherit the authenticated session cookies and TLS signatures required to bypass the firewall.

## GitHub Actions Deployment

This script is deployed as a nightly background job using GitHub Actions. It runs automatically every day at 12:00 AM EST (05:00 UTC).

### Headed Browser in a Headless Runner
Because bypassing Cloudflare requires a headed browser instance (`headless=False`), the script cannot run natively on a standard headless Linux CI/CD runner. To solve this, the GitHub Actions workflow utilizes **Xvfb (X virtual framebuffer)**.

The workflow executes the following steps to simulate a display:
1. Installs Xvfb via `apt-get` on the `ubuntu-latest` runner.
2. Prefixes the Python execution command with `xvfb-run` and standard resolution arguments.
3. This creates a virtual in-memory screen, tricking Playwright into believing a physical monitor is attached so the browser can render properly and process Turnstile checks.

### Workflow Configuration (`.github/workflows/nightly-sync.yml`)

```yaml
name: Nightly AniList Sync

on:
  schedule:
    # 05:00 UTC corresponds to 12:00 AM EST
    - cron: '0 5 * * *'
  workflow_dispatch: 

jobs:
  sync-database:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python Environment
        uses: actions/setup-python@v5
        with:
          python-version: '3.11' 

      - name: Install Python Packages
        run: |
          python -m pip install --upgrade pip
          pip install notion-client python-dotenv playwright
          
      - name: Install Playwright & System Dependencies
        run: playwright install chromium --with-deps

      - name: Install Virtual Display (Xvfb)
        run: sudo apt-get install -y xvfb

      - name: Execute Sync Script
        env:
          NOTION_TOKEN: ${{ secrets.NOTION_TOKEN }}
          NOTION_DATABASE_ID: ${{ secrets.NOTION_DATABASE_ID }}
        run: xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" python sync.py
