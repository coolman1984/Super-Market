# تقرير المراجعة الشاملة — Super-Market (Review Report)

- **Date:** Sat 31 Oct 2026 (Africa/Cairo, UTC+2)
- **Reviewed:** branch `ccr-a6cef312-nbc939` @ `e848838` (== `main` content + 1 commit), plus `origin/cloudflare-sync` @ `6970d8f` (PR #14, read-only)
- **Author:** Grok Bot (read-only reviewer). **No code was changed.** This file is the only change.
- **Reader:** Claude Code. Every issue below has: ID · where · problem · impact · **exact fix**. Fix in severity order; tick the checkbox when done.
- **Owner goal:** sell the program to shops; Windows EXE built by GitHub Actions; wired to Cloudflare (backup now, sync/phones later); everything (customers too) on the server, phones get reports only.

## 0. Executive summary (plain words, for the owner)

The **sales/stock/money core is strong**: 210 Python tests + 9 Worker tests pass, no floating-point money, strict permission checks, safe web UI. The program is **not yet sellable** because:

1. **Backup to the cloud does not work in any installed copy** — the web address built into the EXE points to a Cloudflare Worker that was never deployed (`super-market-backup…`: Cloudflare error 1042). The *other* Worker that **is** live (`super-market-sync…`) is a different design (PR #14) that the EXE does not talk to.
2. **There are two competing cloud designs** (backup-to-R2 on `main`, sync-to-D1 in PR #14). They conflict (9 files) and must become one.
3. **No licence/activation/trial/EULA exists**, and the repository is now **public with no LICENSE file** — nothing stops anybody from copying and running it free.
4. **No release pipeline**: CI builds an installer named `…CI-TEST-NOT-FOR-CUSTOMERS`, unsigned, kept 7 days, never published as a release.
5. A **cashier can refund any sale in cash with no manager approval**; anybody who can reach the server can **lock the owner out** by typing a wrong password 5 times.
6. Printer / scanner / scale / Egyptian e-receipt (ETA) / one full real-shop day are **not verified** (SALE_READINESS gates 6–8 are ⛔).

### Counts

| Severity | Count |
|---|---|
| Critical (blocks selling) | 4 |
| High | 7 |
| Medium | 10 |
| Low | 11 |
| **Total** | **32** |

## 1. What was verified (evidence)

| Check | Result |
|---|---|
| `python3 tools/ci_tests.py` on `ccr-…` | **210 tests OK** (17 s) |
| `cd cloud && npm test` (main's backup Worker) | **9/9 OK** |
| PR #14 (scratch worktree, not pushed): `tests/test_cloudsync.py` 17 OK; `cloud` `npm test` 17/17 OK; full suite 209 OK | OK |
| Fuzz: 400 random carts (weighed meat + units, invoice discounts) through `Shop.sell`; then `reconcile()` | 0 mismatches, `reconcile.ok == true` |
| Huge numbers (`2**63` qty/price/cost/limit/discount) | all rejected cleanly (`bad_number`/`bad_quantity`) — good |
| Live server probe (temp data dir, port 8799, deleted after) | setup/login OK; cookie `HttpOnly; SameSite=Strict`; path traversal → 404; hostname Host header → 421; **lockout DoS reproduced** (H2) |
| i18n | `ar.js` / `en.js` both 1167 keys, no key missing in either; only 4 server error codes (`download_mismatch`, `no_copies`, `not_found`, `rejected` — cloud/restore) have no `err.*` Arabic text (L7) |
| Secrets scan of all remote refs (ghp_/github_pat_/AKIA/private key) | none found |
| CI: `windows-portable` run 41211949449 on `main` | both jobs success; artifacts `SuperMarket-win64`, `SuperMarket-Setup-…CI-TEST-NOT-FOR-CUSTOMERS` (retention 7 days) |
| Downloaded installer artifact | 9.78 MB, sha256 `f6e7d77d…2aaf6`, **no Authenticode signature** (cert table size 0) |
| GitHub releases / tags | **none** |
| Live Worker `https://super-market-sync.mflma2040.workers.dev` | `GET /` → `{"ok":true,"service":"super-market-sync","version":"1.1.0"}`; `/v1/push`, `/v1/pull`, `/v1/commands`, `/v1/health` unsigned → **401**; `/admin/stores` wrong key → **401**; headers `cache-control: no-store`, `nosniff`; client UAs `SuperMarket/…` and `SuperMarket-CloudCopy/1` → 200 (Cloudflare blocks `Python-urllib` UA with 1010, so every client must keep a custom UA) |
| Live Worker `https://super-market-backup.mflma2040.workers.dev` (built into the EXE) | **Cloudflare 1042 = no Worker deployed** |

**Not verified / limits (be honest with the owner):** I could not run the Windows EXE or a real printer/scanner/scale. I did **not** write test data into the live Worker/D1 (to avoid touching the owner's database), so a full *push → pull → restore* round-trip against the live service was **not** re-run by me; PR #14's own tests and doc report that. I did not read every line of the ~27k lines: reviewed fully were `auth`, `app`, `core`, `db`, `backup`, `cloud`, `launcher`, installer/CI/build scripts, both Workers, and the money/stock/refund/shift/approval parts of `services.py`; skimmed: production/recipes/counts/promotions/thaw (rely on tests), the UI views (grep-level: no `innerHTML`/`eval`, CSV formula guard present).

---

## 2. CRITICAL

### [ ] C1 — Installed program's cloud address points to a Worker that does not exist
- **Where:** `server/cloud.py:48` `BUILT_IN_URL = "https://super-market-backup.mflma2040.workers.dev"`; `server/cloud.py:~422` (`cloud_url(compiled)` returns it only for the EXE); `cloud/wrangler.toml:8` `database_id = "REPLACE-WITH-THE-ID-FROM-wrangler-d1-create"`; `docs/SALE_READINESS.md` admits "not deployed".
- **Problem:** the compiled EXE tries this URL hourly/nightly; it answers Cloudflare error 1042. No customer copy is ever uploaded. The only deployed Worker is `super-market-sync` (different API: `/v1/*`, not `/pc/register`).
- **Impact:** every sold installation has **no off-site backup**, silently (disk failure/theft/ransomware = total loss). Also the R2 design needs a payment card on Cloudflare (owner said pay nothing).
- **Fix:** decide the single design (see C2). Whatever wins: (a) deploy it, (b) make `BUILT_IN_URL` equal the deployed hostname, (c) add a CI/unit test that fails if `BUILT_IN_URL`'s host differs from `name` in `cloud/wrangler.toml` (+ account subdomain from a constant), (d) the *owner-facing* status screen must show a red "backup NOT working" banner after N failed attempts / 24 h without a successful upload (verify `cloud.` status keys actually surface this on the home screen), (e) the D1 `database_id` is not a secret: commit the real id into `wrangler.toml` once the database is created.

### [ ] C2 — Two incompatible cloud implementations; PR #14 conflicts with main
- **Where:** `main`: `cloud/src/worker.js` (R2 + D1 backup, `/pc/register`, per-PC HMAC derived from `MASTER_KEY`), `server/cloud.py`. `origin/cloudflare-sync` (PR #14): `cloud/src/worker.js` v1.1.0 (D1 only, signed `/v1/push|pull|commands`, one-time pairing codes, admin key, tombstones, version-wins rows), `server/cloudsync.py`, `tools/syncctl.py`.
- **Problem:** `git merge-tree` shows 8 add/add conflicts in `cloud/*` (`deploy.bat`, `deploy.mjs`, `dev/d1.js`, `dev/server.js`, `package.json`, `schema.sql`, `src/worker.js`, `wrangler.toml`) + content conflicts in `launcher.py`, `docs/USER_GUIDE.md`, `web/guide/catalogue.json`. PR #14's `launcher.py` still uses the old pre-ProgramData layout (`ROOT`), so `start_cloud_sync(data, …)` was written for the portable folder only. The installed EXE (Nuitka) has no way to run `syncctl.py join` (tools are not compiled in; no Start-menu entry), so **the installed program cannot join the sync design at all**.
- **Impact:** the owner's real goal (all data on server + phones read reports) cannot ship; two Workers/two D1 schemas confuse support.
- **Fix:** (1) Choose PR #14's D1 design as the single cloud (no card needed; matches "all on server, phones reports"). (2) On a **new branch from current `main`**, port `cloudsync.py` + Worker v1.1.0 over, delete the R2 backup Worker/`server/cloud.py` OR keep `cloud.py` only as a *restore-from-snapshot* helper that talks to the new Worker; keep ONE `cloud/` folder. (3) Re-integrate into the **current** `launcher.py` (`HOME`, `COMPILED`, `start_cloud`) and add Start-menu entries (`SuperMarket.exe cloud-join`, `cloud-code`, `cloud-restore`) so non-technical owner can pair from the installed EXE, typing the one-time pairing code. (4) Keep `ensure_capture` cost-free when disabled (it is). (5) Run both test suites + the installer journey in CI. (6) Close PR #14 only after the new PR merges (owner approves merge with «انشر»).

### [ ] C3 — No licence, activation, trial, EULA; public repo without LICENSE
- **Where:** repo-wide (`git grep -i licen` finds only docs); `gh repo view` → `visibility: PUBLIC`, `licenseInfo: null`; `installer/super-market.iss` has no `LicenseFile=`; `CLAUDE.md` rule 12 says it is missing.
- **Problem:** nothing limits use per shop/PC, no trial, no expiry, no EULA/terms shown at install; the source is public so anyone can run `start.bat` from the source/portable zip free. Third-party notices exist only in the portable build (`LICENSES/`), not in the installer.
- **Impact:** cannot legally or technically sell/protect; commercial risk; unclear rights for customers (no licence file = "all rights reserved" but no grant either).
- **Fix:** (1) **Owner decision needed first:** move product source to a **private** repo (or keep public only a marketing/docs repo) — a public repo defeats any licensing. (2) Add `LICENSE` (proprietary EULA, Arabic+English) and `LicenseFile=` in the `.iss` + a "third-party notices" page (Python PSF, Nuitka output, IBM Plex fonts OFL, etc.) in the installer. (3) Implement activation on the (single) Worker: D1 table `licenses(key, shop, plan, max_devices, expires_at)` + `activations(device_id, key)`; client stores a **signed** token (HMAC server-side or Ed25519 verified by a vetted lib; do **not** invent crypto) with 14/30-day offline grace. (4) Behaviour on expiry: **never block reading, exporting, backups or finishing an open shift**; block only opening a new shift/new sale after the grace + clear Arabic message. (5) Trial = 14 days from first activation. (6) Tests for: valid/expired/offline-grace/clock-rollback/transfer. (7) State plainly in docs that determined users can bypass any client-side check (a deterrent, not DRM).

### [ ] C4 — No customer-grade release pipeline; installer unsigned; artifacts expire
- **Where:** `.github/workflows/windows-portable.yml`, `installer/super-market.iss`, `tools/build_windows.py`.
- **Problem:** CI only produces `SuperMarket-Setup-<ver>-CI-TEST-NOT-FOR-CUSTOMERS.exe` (+ marker txt), artifact retention 7 days, **no tag/release workflow, no checksum publication, no code-signing**, no commercial channel build is ever executed in CI (only the test channel). Version `0.1.0-pilot` is hard-coded.
- **Impact:** the owner cannot download a stable, signed, sellable `SuperMarket-Setup.exe`; Windows SmartScreen shows "unknown publisher" to every customer.
- **Fix:** add job `release` triggered on tag `v*`: build the **commercial** channel (without the CI-TEST suffix) after the journey test, produce `SHA256SUMS`, create a GitHub Release (draft) attaching installer + portable zip; sign with Authenticode (`signtool`, certificate in GitHub **encrypted secrets**, never in repo; cheapest options: OV/EV cert or Azure Trusted Signing — owner must approve any cost); timestamp the signature; keep CI-TEST naming for non-tag builds; bump version from the tag. Add a CI step that verifies `Get-AuthenticodeSignature` is `Valid` on tag builds.

---

## 3. HIGH

### [x] H1 — Any cashier can refund any sale in cash without manager approval (fraud)
- **Where:** `server/services.py:1710` `return_sale` (approval only for `change_of_mind` on raw meat/prepared: ~line 1752); `:1691` `sale_detail` lets anyone with `refund` read any sale; no age limit, no receipt-owner check, no threshold. Reproduced in-process: `cash1` refunded `cash2`'s sale, 4,500 piastres in cash, no approval.
- **Impact:** classic till theft: a dishonest cashier takes cash out against old/other sales; drawer count then "balances" because a refund row exists.
- **Fix:** add settings `refund_approval_over` (piastres, default e.g. 20000) and `refund_days_without_approval` (default 7); in `return_sale`, require `self._approved(actor, approver, "release_hold"-like perm "approve_refund")` when `total > threshold` OR sale older than N business days OR sale belongs to another cashier OR method=cash and reason in (`other`,`change_of_mind`). Log in audit with approver. Add tests (cashier alone → `approval_required`; with manager approval → ok; own recent small refund → ok). Show the rule in the Arabic UI.

### [x] H2 — Anyone who can reach the server can lock the owner out (verified)
- **Where:** `server/auth.py:16-17,86-101` (`MAX_FAILED=5`, `LOCK_MINUTES=5`, lock is per username, applies to the owner too, `/api/login` is unauthenticated).
- **Impact:** on LAN mode (or any local user) 5 bad tries per 5 minutes keep the owner/manager permanently locked out; reproduced: after 6 bad attempts the correct password returned `account_locked`.
- **Fix:** key throttling by (username + client IP + workstation) and add a global per-IP limiter (HTTP 429, exponential back-off); do not lock the account itself for >60 s unless failures are from the same IP; allow the owner to unlock from the shop PC (`127.0.0.1`) via existing recovery. Test: 100 bad logins from IP B never block IP A.

### [ ] H3 — Hardware & real-shop validation missing (SALE_READINESS gates 6, 8)
- **Where:** `docs/SALE_READINESS.md`, `docs/FIELD_TEST.md`.
- **Problem:** Arabic thermal receipt printing, barcode scanner behaviour (keyboard-wedge speed, prefixes), label scale/weighed barcodes, cash drawer — only simulated. No non-technical person ran a full day.
- **Impact:** first customer finds printing/scanning problems; reputation risk.
- **Fix:** (owner/hardware task, not code) run `docs/FIELD_TEST.md` on the shop PC with the real printer/scanner/scale; record results in a new `docs/FIELD_TEST_RESULTS.md`; Claude: add a one-screen "device test" page with print-test receipt (Arabic + 58/80 mm), scanner test, and scale-barcode test, whose result is stored.

### [ ] H4 — Egyptian tax/e-receipt compliance undefined (gate 7)
- **Where:** receipts say "ليس فاتورة ضريبية"; `tax_mode` only allows `"unverified"` (`services.py` `SETTING_RULES`).
- **Impact:** a shop that must issue ETA e-receipts/e-invoices cannot legally use the program as its till; blocks sale to registered shops.
- **Fix:** owner/accountant must confirm obligations (Egyptian Tax Authority e-receipt rules for retail). Then implement as a separate module (receipt number sequence per ETA, tax lines/VAT rates per product, export). Until then, ship with a clear contract/UI statement that the program is an internal management system, not an ETA-compliant invoicing system. (Do not guess tax rules in code.)

### [x] H5 — Only off-box backup is the (broken) cloud; no USB/second-disk copy
- **Where:** `launcher.py:133-160` (`daily_backup` → `HOME/backups-shop` on the same disk), `server/backup.py`.
- **Impact:** with C1 unresolved, a failed disk loses everything; even after C1, an offline shop has no external copy.
- **Fix:** add setting `backup_folder_2` (any path, e.g. USB/second drive/network share) and copy each verified daily backup there (`shutil.copytree`, verify manifest hash, skip silently when drive missing, show a yellow banner "no external copy for N days"). Include in Start-menu a "نسخة على فلاشة" action. Tests with a tmp folder.

### [x] H6 — Installer gives data access to one Windows account but starts for all users
- **Where:** `installer/super-market.iss:96,159` (icacls: SYSTEM + Administrators + the installing operator) vs `:109` autostart in `{commonstartup}` (all users).
- **Impact:** a second Windows login (cashier's account) starts the EXE, cannot open `ProgramData\SuperMarket` → "program could not start"/"not writable". Common in shops that use a standard cashier Windows account.
- **Fix:** grant `Modify` to the local `Users` group (`*S-1-5-32-545`) **only for the data/log/backup folders** (not the program folder) or run the server as a per-machine background service/scheduled task under one account and let other users only open the UI; add an installer-journey step that starts the EXE as a second standard user (CI can create a local user with `net user`).

### [ ] H7 — Backup design on `main`: open `/pc/register`, unencrypted backups (applies if R2 design is kept)
- **Where:** `cloud/src/worker.js:56 register`, route at `:177`; `docs/SALE_READINESS.md` ("content not encrypted").
- **Problem:** anyone on the internet can register PCs (limits: 200 PCs/day, 4 GB each ⇒ storage abuse and a global quota DoS for real new shops). Backups (customers' names/phones, sales, password hashes) are stored **unencrypted** in R2; the "recovery code" is a bearer secret that reads all copies.
- **Fix:** if C2 chooses D1 sync, delete this Worker. Otherwise require an owner-issued registration token (admin key → one-time code like PR #14 pairing), lower per-store quota, and encrypt backups client-side with a vetted AEAD (e.g. `cryptography` AES-GCM bundled, key derived from the owner's recovery code via scrypt) — do not hand-roll.

---

## 4. MEDIUM

### [x] M1 — LAN mode is plain HTTP (passwords and session cookie visible on Wi-Fi)
- **Where:** `server/app.py` (`serve(... lan=True)`), cookie set at `:562` has no `Secure` (can't over HTTP).
- **Fix:** document and enforce "wired/isolated LAN only"; show a warning in the LAN-start window; optionally offer HTTPS with a locally generated self-signed cert + install-time trust. Add idle-session timeout for tills (verify the server-side screen lock covers it).

### [x] M2 — LAN access by computer name rejected
- **Where:** `app.py` Host allow-list (probe: `Host: shop-pc:8799` → 421 `bad_host`).
- **Impact:** tills must use the numeric IP; breaks when DHCP changes.
- **Fix:** allow the PC's own hostname + `<hostname>.local` and private-range IPs in LAN mode; show the correct address in the startup message; recommend a fixed IP.

### [x] M3 — Approval-password guessing at the till
- **Where:** `services.py:~740-750` (`_approval_failures`: 5 per 5 min per requesting user, **in memory**, reset at restart).
- **Impact:** a cashier can try ~1,440 manager passwords/day; restart resets.
- **Fix:** persist failures in DB, 5 failures ⇒ exponential lock (15 min → 24 h) for the *requester*, audit each failure, notify owner.

### [x] M4 — No socket timeout on the HTTP server (slow-connection DoS)
- **Where:** `server/app.py:~587` (`ThreadingHTTPServer`, `daemon_threads=True`, no handler `timeout`; `rfile.read(length)` at `:512`).
- **Fix:** set `Handler.timeout = 15`, read body with deadline, cap concurrent connections (e.g. 64); test with a client that sends headers and stalls.

### [x] M5 — Clock set behind a closed business day refuses everything shop-wide
- **Where:** `server/db.py:113-122 business_day_for` (`today() < closed` → `day_closed` for everyone).
- **Impact:** a dead CMOS battery/wrong date freezes the shop with an unclear message.
- **Fix:** detect at start-up and show a dedicated Arabic screen "ساعة الجهاز خاطئة — صحّحها (التاريخ الصحيح …)"; allow the owner to continue only after correcting; never auto-post to the past. Add test that the message names the closed date.

### [x] M6 — Backup/restore ordering depends on the PC clock; renaming the PC creates a new cloud space
- **Where:** `server/backup.py:85-87` sorts manifests by folder name (`%Y%m%d-%H%M%S` of PC clock); `server/cloud.py` PC identity/space derived from name.
- **Impact:** wrong clock ⇒ newest copy pruned/skipped; renaming Windows PC orphans old cloud copies.
- **Fix:** order by a monotonic sequence stored in each manifest (`seq` from `ops` max id + counter), not by name; key cloud space on a random `instance_id` (already in settings) not the hostname.

### [x] M7 — Local backup retention only prunes `daily`
- **Where:** `launcher.py:47,146` (`KEEP_DAILY=30`); `before-<version>` and manual copies never pruned; no disk-space check.
- **Fix:** keep last N `before-*` (e.g. 5), warn when free disk < 2 GB or backups > X GB, skip with logged error instead of failing silently.

### [ ] M8 — D1 free-tier claims are optimistic (PR #14 docs)
- **Where:** `docs/CLOUD_SYNC.md` quota estimate; `cloud/schema.sql` (secondary indexes on `records` multiply row-writes).
- **Fix:** recompute "writes/day = rows × (1 + indexes)" and state shops/day supported; coalesce multiple changes of the same row in one batch; throttle bootstrap (first full copy of a big DB) across days; surface "quota exhausted" to the owner.

### [ ] M9 — Pairing/admin rate limits can be used for denial of service (PR #14 Worker)
- **Where:** `origin/cloudflare-sync:cloud/src/worker.js` (pairing + admin throttles keyed globally).
- **Fix:** key throttles by IP (`CF-Connecting-IP`) and by store; keep admin endpoint locked after 10 failures only for that IP; add Cloudflare WAF rate-limit rule on `/admin/*` and `/v1/pair`.

### [x] M10 — Generic `ValueError → 400` hides real bugs
- **Where:** `server/app.py` request handler (broad `except ValueError`).
- **Fix:** catch only `ShopError`/`json.JSONDecodeError` for 400; map unexpected exceptions to 500 + log with traceback + request id; add a test that an injected `ValueError` inside a service surfaces as 500 in logs.

---

## 5. LOW

- [x] **L1** `server/domain.py` is dead code (tests `test_domain.py` only). Delete or wire it; update CLAUDE.md.
- [x] **L2** Promotions use calendar `today()` rather than the business day (`services.py` promotion lookup) — a promo ending "today" stops at midnight though the shop's day continues until `day_start_hour`. Use `self.store.business_day_for(...)`.
- [ ] **L3** Plain `http://` request to the live Worker returns 500 instead of redirect/400 (`cloudflare-sync` Worker). Return 400/redirect to https; enable "Always Use HTTPS".
- [x] **L4** Pre-upgrade copy folder uses minute resolution; two upgrades in one minute would collide (`before-<version>`). Include seconds.
- [x] **L5** `GET /api/state` is unauthenticated and returns shop name + instance id (needed by launcher). Return only `version/mode/setup_needed` unless the caller is local (127.0.0.1) or logged in.
- [x] **L6** PBKDF2 iterations 240,000 (`auth.py:14`) < OWASP 2023 600,000 for PBKDF2-SHA256. Raise to ≥600k; hashes carry `pw_iter` so existing users re-hash on next login (verify the rehash-on-login path exists, else add).
- [ ] **L7** Four server error codes lack Arabic `err.*` text: `download_mismatch`, `no_copies`, `not_found`, `rejected` (cloud restore). Add to `ar.js`/`en.js` (+ a test comparing `ShopError` codes to `err.*` keys, `tests/errorcodes.py` could be extended).
- [x] **L8** `add_party` has no duplicate check (double click creates two customers) and phone isn't validated (`services.py:~221`). Normalize phone (Egyptian mobile pattern) and warn on same name+phone.
- [ ] **L9** `tools/ci_tests.py` is the only working test entry; `python -m unittest discover -s tests -t .` fails (tests is not a package). Add `tests/__init__.py` or document in README/CLAUDE.md (`python3 tools/ci_tests.py`).
- [ ] **L10** Closing the business day early (before the shop stops selling) pushes the rest of the day's sales into tomorrow; no "reopen". Add a confirmation dialog stating this in Arabic, and an owner-only audited "reopen last day" if no later day has data.
- [ ] **L11** Return restock puts the whole quantity into the first allocation's location and a single lot with the earliest expiry (`services.py:~1757-1768`) even when the sale drew from several lots/locations. Safe (earliest expiry) but loses precision; split per allocation proportionally if stock accuracy matters.

---

## 6. Windows EXE built by GitHub Actions — verdict

- **Yes, it is built by Actions** (`windows-portable` workflow): Nuitka → `SuperMarket.exe` → Inno Setup installer; runs install → sale journey → upgrade → uninstall on a clean Windows runner; leak check; portable zip with NuGet Python. Last `main` run 41211949449: success.
- **Gaps:** C4 (no release/tag/signing, 7-day artifacts, CI-TEST name, no commercial channel build), H6 (second Windows user), C1 (built-in cloud URL dead), C3 (no EULA/licence), SmartScreen warning, I could not run the EXE myself (Linux box). The CI journey runs with `SM_CLOUD_URL=off`, so **cloud behaviour of the real EXE is never tested in CI** — add a CI job that runs the EXE against a local mock Worker (the `cloud/dev/server.js` dev server exists) to prove backup/restore end-to-end.

## 7. Cloudflare end-to-end — verdict

| Piece | Status |
|---|---|
| `super-market-sync` Worker (PR #14 design) | **Live, v1.1.0, secure by default** (unsigned calls 401, admin key required, `no-store`, `nosniff`). Admin key in `~/.secrets/sm-sync` validated read-only (404 on a non-existent store), never printed. |
| `super-market-backup` Worker (built into EXE) | **Not deployed** (1042) |
| EXE ⇄ Worker | **Not connected**: EXE → backup URL (dead); sync client exists only on unmerged PR #14 and only for the portable/source layout |
| Full push→pull→restore on live D1 | **Not run by me** (no writes to the owner's DB); PR #14 tests (17 py + 17 worker) pass locally against the dev D1 shim |
| Phones | Design exists (read-only reports published by the shop PC; commands signed); no phone UI yet |

**Order of work for Claude:** C2 → C1 → C4 → C3 → H1 → H2 → H6 → H5 → H7(if kept) → M* → L*.

## 8. Positives (keep these)

Integer piastres/grams with half-up and largest-remainder allocation, no floats; idempotent commands with fingerprints; audit log; FEFO + lot/recall traceability; refund caps per payment channel; strict RBAC with server-side checks; CSRF (`X-SM` + Origin) and DNS-rebinding (Host) guards; no `innerHTML`/`eval` in the UI; CSV formula guard; SQLite WAL + `synchronous=FULL`; owner recovery only on the shop PC; stop-key file for stopping; Worker fails closed; HMAC-signed requests with nonce; Nuitka source-leak check; installer journey test in CI; Arabic/English dictionaries in exact parity.
