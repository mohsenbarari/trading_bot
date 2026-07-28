# Browser selection for the Telegram Selenium scraper

**Decision: `chrome-headless-shell` 147.0.7727.24**, from Google's official
Chrome for Testing bucket, driven by the exactly-matching `chromedriver`.

Install: `scripts/install_scraper_browser.sh`
Use: `core/scraper_browser.ScraperBrowser`

Target workload: two Telegram accounts scraped concurrently — two groups on
account 1, one channel on account 2 — which means two browsers alive at once,
each with its own persistent login.

---

## What was actually on the host

The build host had **no Chrome, no Firefox and no Edge** — `dpkg`, `/usr/bin`,
`/usr/local/bin`, `/opt` and `/snap/bin` were all clean. The only browser
present was a Playwright Chromium 141 bundle under `/opt/pw-browsers`, and the
only driver was `chromedriver` 147 on `PATH`, which cannot drive Chromium 141
(chromedriver requires a matching major version).

So nothing installed was reusable and a download was required regardless. If
your own server really does have Firefox or Edge, the comparison below still
argues for installing the headless shell anyway — it is the lighter binary.

## Why chrome-headless-shell

`chrome-headless-shell` is Chrome's headless-only binary: same Blink and V8,
but no browser UI layer, no profile chrome, no extension host. It is the
lightest binary that ChromeDriver can drive, so Selenium keeps working
unchanged.

| Candidate | Verdict |
| --- | --- |
| **chrome-headless-shell** | **Chosen.** Lightest; full Selenium support. |
| Full Chrome / Chromium `--headless=new` | Works, but ~50% more memory for identical output. |
| Microsoft Edge | Chromium underneath, so at best equal to Chrome and never lighter — it ships extra services on top. `msedgedriver.microsoft.com` is also blocked by this environment's egress policy, so it could not be driven here at all. |
| Firefox + geckofdriver | Not measurable here: `ftp.mozilla.org` and `download.mozilla.org` are both blocked by egress policy. Excluded on availability, not on merit. |

Only Chrome-family options could be benchmarked. That limitation is real and is
called out rather than papered over — but the headless shell beats full Chrome
on every axis measured, and Firefox would have had to beat the *shell*, not
full Chrome, to win.

## Measurements

Same Chrome build (147.0.7727.24) and the same chromedriver for every row, so
the binary is the only variable. Peak **PSS** is the honest number for Chrome:
plain RSS double-counts memory shared between the browser's processes. Median
of 3 runs, 4000-message list, scrolled and scraped.

### One browser

| binary | peak PSS | peak RSS | procs | startup | scrape |
| --- | --- | --- | --- | --- | --- |
| **chrome-headless-shell** | **291 MB** | 549 MB | 7 | **0.15 s** | 6.06 s |
| full Chrome `--headless=new` | 444 MB | 967 MB | 9 | 0.46 s | 7.39 s |
| full Chrome `--headless=old` | 439 MB | 954 MB | 9 | 0.32 s | 5.47 s |

### Two accounts at once — the real shape of this job

| scenario | peak PSS | peak RSS | procs |
| --- | --- | --- | --- |
| **2x chrome-headless-shell** | **439 MB** | 1070 MB | 12 |
| 2x full Chrome `--headless=new` | 669 MB | 2082 MB | 18 |

**Saves 231 MB (34%)** on the two-account setup, and about 1 GB of RSS.

### Install footprint

| | download | on disk | shared libs |
| --- | --- | --- | --- |
| chrome-headless-shell | 112 MB | 257 MB | 52 |
| full Chrome | 171 MB | 370 MB | 87 |

Fewer library dependencies also means fewer distro packages to install on a
minimal server.

### Flag tuning

The lean flag set in `core.scraper_browser.LEAN_FLAGS` is what produced the
numbers above. A further-tuned set (image blocking, `--renderer-process-limit=1`,
a V8 heap cap, extra `--disable-features`) measured **291 MB vs 292 MB** —
no gain, because the synthetic benchmark page has no images or web fonts to
suppress. Image blocking is kept on by default anyway (`load_images=False`),
since a real Telegram chat is full of avatars and media, where it should
actually pay off. That saving is expected, not measured.

## The `--user-data-dir` trap

Persistent profiles are not optional here: a Telegram Web login lives in
localStorage and IndexedDB, so without a stable profile every restart means
re-scanning a QR code. And each account needs its *own* profile — pointing two
accounts at one directory makes them clobber each other's session.

**chrome-headless-shell cannot be launched by chromedriver with a
caller-supplied `--user-data-dir`.** It fails with:

```
SessionNotCreatedException: unable to discover open pages
```

Traced through chromedriver's verbose log, the cause is: chromedriver appends
an initial `data:,` page **only when it creates its own throwaway profile**.
Given a profile, it appends nothing, the shell opens zero tabs, `/json/list`
returns `[ ]`, and chromedriver gives up. Chromedriver also strips bare URL
arguments, so the initial page cannot be injected through `ChromeOptions`.

Confirmed non-fixes: `--no-first-run`, `--no-default-browser-check`,
`--restore-last-session`, `--homepage=`, and a trailing `about:blank` or
`data:,` passed via `add_argument`.

**The fix** — implemented in `core/scraper_browser.py` — is to start the
browser ourselves with the profile *and* an explicit `about:blank`, then attach
Selenium over `debuggerAddress`. Verified: localStorage **and** IndexedDB both
survive a full browser restart.

The launcher uses `--remote-debugging-port=0` and reads the real port back from
the profile's `DevToolsActivePort` file, rather than picking a port itself and
racing another process for it.

## Verified working

Everything Telegram Web depends on is present in the headless shell:
localStorage, IndexedDB, Service Workers, WebSocket, WebAssembly,
`crypto.subtle`, and Web Workers.

The default user agent advertises `HeadlessChrome`. Override it via the
`user_agent=` argument if that turns out to matter.

## Caveats

- `web.telegram.org` is blocked by this environment's egress policy, so the
  benchmark used a locally served 4000-row stand-in shaped like a Telegram
  message list. The relative comparison is sound — identical workload, identical
  driver — but absolute memory on the real client will be higher, and the
  browser has not yet been pointed at Telegram itself.
- Numbers come from a 4-core / 15 GB x86_64 Ubuntu 24.04 host.

## Worth knowing before building on this

Selenium is not the cheapest way to read Telegram. Telegram's own MTProto API
(Telethon / Pyrogram) needs no browser at all — tens of MB instead of the
439 MB measured above, no QR-code sessions, no DOM scraping to re-fix whenever
Telegram ships a redesign, and it can read group and channel history directly.

If Selenium was chosen because scraping must look like a normal web client, or
because the accounts cannot be used through the API, then the headless shell
above is the right way to do it. But if that constraint does not actually
apply, MTProto is roughly an order of magnitude cheaper on exactly the resource
this decision was optimising for.
