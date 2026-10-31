# CLAUDE_FIX_PROMPT — Super-Market: every remaining problem, in fix order

You are Claude Code working in the repository `coolman1984/Super-Market`, on branch `cloudflare-sync` (PR #14). Read this whole file before you change anything. It is self-contained.

## 0. Context (plain facts)

- **Product:** an Arabic-first (RTL), bilingual supermarket / butcher till program for Egyptian shops. Python standard-library HTTP server + SQLite (WAL, `synchronous=FULL`) + vanilla JS UI in `web/`. Money is integer **piastres**, weights integer **grams**; never use floats for money or weight. Windows build: Nuitka -> `SuperMarket.exe` -> Inno Setup installer (`tools/build_windows.py`, `installer/super-market.iss`), plus a portable zip (`tools/build_portable.py`). CI: `.github/workflows/windows-portable.yml` (Windows runner), `cloud-sync.yml` (Worker + client tests), `browser-review.yml`.
- **Owner:** Mohamed Fawzy (`coolman1984`), Egyptian, **not a programmer**. He wants to **SELL** this program to shops. He wants: a Windows EXE built by GitHub Actions; every shop's data copied to **his own Cloudflare account**; customers' data also on the server; phones get **reports only** (read-only).
- **Cloud design (decided, already implemented on this branch):** ONE design = D1 sync. Worker `cloud/src/worker.js` v1.1.0 (live at `https://super-market-sync.mflma2040.workers.dev`), client `server/cloudsync.py`, vendor tool `tools/syncctl.py`, migration 9 (`sync_changes`, `sync_meta`). The R2 "backup" design (`server/cloud.py`, `/pc/register`, host `super-market-backup...`) was **deleted**. The compiled EXE has the Worker address built in (`cloudsync.BUILT_IN_URL`); a shop is connected with one vendor-issued code `SMC2-<store_id>-<one-time code>` typed in Start menu -> "ربط بالسحابة"; the app then copies every change automatically; Start menu also has status / restore; Settings -> cloud card shows the status. Source runs never talk to the cloud unless `SM_CLOUD_URL` is set; `SM_CLOUD_URL=off` forbids it (CI and test machines). Passwords are never uploaded (restored users get `pw_hash='!restored'`, `must_change=1`).
- **Cloudflare facts you must respect:** Cloudflare blocks the default `Python-urllib` User-Agent with error 1010, so every client request uses a custom UA. D1 free tier counts index writes. R2 needs a payment card (owner pays nothing without asking). Error 1042 = no Worker at that hostname.
- **Done and verified on this branch (do not redo, do not regress):** the wiring above; 239 Python tests + 17 Worker tests + browser UI scripts pass; a live smoke test against the deployed Worker with a throwaway store (join -> push -> `complete: true` -> restore on a new folder -> 0 foreign-key problems -> unsigned request 401 -> devices revoked).

## 1. Hard rules

1. **No secrets in the repo, logs, commits, PR text or chat.** Never print or commit `ADMIN_KEY`, `MASTER_KEY`, device secrets, GitHub tokens, Cloudflare API tokens, signing certificates. Use GitHub *encrypted secrets* / Cloudflare secrets only.
2. **Never force-push. Never merge.** Open/update PRs only; the owner merges after he says «انشر».
3. **Pay nothing, buy nothing.** Anything with a cost (code-signing certificate, paid Cloudflare plan, domain) -> write the options and prices in the PR and STOP.
4. Do not touch the owner's other projects or any real shop's data. Tests that need the cloud must use the local dev Worker (`cloud/dev/server.js`) — never the live Worker.
5. Arabic UI text: formal Modern Standard Arabic (no Egyptian slang: tests in `tests/test_language.py` ban words such as عشان/دلوقتي/مفيش). `web/js/i18n/ar.js` and `en.js` must keep the **exact same keys** (parity is tested). Every new `ShopError` code needs `err.<code>` in both dictionaries (add a test that scans `ShopError("…")` codes against the dictionaries — see L7).
6. Money rules: integer piastres, half-up rounding, largest-remainder allocation; stock changes only through the existing ledger functions; every command idempotent via `key`; every sensitive action audited.
7. Run **all** tests before every push (section 2). One commit per issue ID, message `Fix <ID>: <what>`. Update `docs/` (Arabic user/support guides, `docs/SALE_READINESS.md`, `DEVELOPMENT_HISTORY.md`) when behaviour changes.

## 2. How to run everything

```
python3 tools/ci_tests.py                      # all Python tests (~20 s). Do NOT use `unittest discover` (see L9).
cd cloud && npm test                           # Worker tests (17)
node tests/ui/audit_ui.js                      # static UI audit
sh tests/ui/run.sh /tmp/shots                  # browser tests; needs `playwright` for node (NODE_PATH) and chromium
cd tests && python3 -m unittest test_cloudsync test_cloud_wiring   # cloud client, uses local dev Worker (needs node >= 22 with node:sqlite)
```
Windows CI (`windows-portable`) also builds the EXE, installs it silently, runs `tools/journey_exe.py`, updates over it, uninstalls. Check it with `gh run list --branch cloudflare-sync` / `gh run watch`. Report times to the owner in Africa/Cairo (UTC+2).

## 3. Order of work

`C3 -> C4 -> H1 -> H2 -> H6 -> H5 -> E1 -> E2 -> E3 -> H3/H4 (owner tasks, only the code parts) -> M* -> L*`. Items marked **OWNER DECISION** need his answer first: implement only what is safe without it, document the question, and continue.

---

## 4. CRITICAL (blocks selling)

### C3 — No licence, activation, trial or EULA; the repository is PUBLIC with no LICENSE file
- **Where:** repo-wide; `gh repo view` shows `visibility: PUBLIC`, `licenseInfo: null`; `installer/super-market.iss` has no `LicenseFile=`; third-party notices (`LICENSES/`) exist only in the portable zip.
- **Problem:** nothing limits use per shop/PC, no trial, no expiry, no terms shown at install. Anyone can download the source or zip and use it free; with no LICENSE file customers also get no clear grant.
- **Impact:** the program cannot legally or technically be sold or protected.
- **Fix:**
  1. **OWNER DECISION (do not make the repo private yourself):** state in the PR that a public repository defeats any licensing; recommend a private repo (or a public docs/marketing repo only). Until he decides, add a proprietary `LICENSE` + `EULA.ar.md` / `EULA.en.md` (Arabic + English: one shop, no resale, no reverse engineering, backups/export rights, data ownership stays with the shop, Cloudflare processing disclosed).
  2. Installer: `LicenseFile=` pointing at the bilingual EULA (user must accept), plus a "third-party notices" page (Python PSF licence, Nuitka runtime, IBM Plex fonts OFL, etc.). Copy `LICENSES/` into the installed folder.
  3. Activation on the existing Worker (one Worker only): D1 tables `licenses(key TEXT PK, store_id, plan, max_devices, expires_at, created_at)` and `activations(device_id, license_key, first_seen, last_seen)`; admin endpoints `POST /admin/licenses`, `POST /admin/licenses/revoke` (admin key only); device endpoint `POST /v1/license` (signed like all `/v1/*`) returning a **signed token** `{store, plan, expires, issued}`. Use a vetted primitive (Ed25519 via WebCrypto in the Worker; verify in the client with the stdlib-free approach you can ship — if no vetted verify library can be bundled, use HMAC with a per-device secret fetched over the signed channel and say so honestly). **Do not invent crypto.**
  4. Client: `server/license.py` stores the token in the data folder, re-checks online at most daily, **14-day offline grace**, trial = 14 days from first activation. Clock-rollback detection (store the highest seen time).
  5. On expiry **never block**: reading, reports, export, backups, finishing an open shift, cloud restore. Block only *opening a new shift / starting a new sale*, with a clear formal-Arabic message that names the date and who to call.
  6. State plainly in `docs/SALE_READINESS.md` that a determined user can bypass any client-side check (deterrent, not DRM).
- **Acceptance tests:** valid token -> sales allowed; expired + inside grace -> allowed, banner shown; expired + outside grace -> new sale refused with `err.license_expired`, but export/backup/restore/close-shift work; clock set back -> refused/banner; revoked licence -> refused after next check; trial day 15 -> refused; two devices on a `max_devices=1` licence -> second refused; offline for 3 days -> still works. Worker tests for every endpoint including unsigned (401), wrong admin key (401), duplicate key. UI string parity test passes.

### C4 — No customer-grade release pipeline; installer unsigned; CI artifacts expire after 7 days
- **Where:** `.github/workflows/windows-portable.yml` (`retention-days: 7`, `TO_BUILD_CHANNEL: ci-test`), `installer/super-market.iss`, `tools/build_windows.py:72` (ci-test channel), version `0.1.0-pilot` hard-coded.
- **Problem:** CI only produces `SuperMarket-Setup-<ver>-CI-TEST-NOT-FOR-CUSTOMERS.exe`. No tag/release workflow, no checksums published, no code signing, so Windows SmartScreen shows "unknown publisher".
- **Fix:** new workflow `release.yml` triggered by tag `v*`: run all tests, build the **commercial** channel (no CI-TEST suffix, own AppId), run the same install/journey/update/uninstall steps, produce `SHA256SUMS`, create a **draft** GitHub Release attaching installer + portable zip + sums. Version comes from the tag (`0.1.0-pilot` -> from tag `v0.1.0`). Add signing steps that are **skipped with a clear message while the secrets are absent** (`WINDOWS_CERT_PFX_BASE64`, `WINDOWS_CERT_PASSWORD` as encrypted secrets; or Azure Trusted Signing) and, when present, sign with `signtool` + RFC3161 timestamp and fail the job if `Get-AuthenticodeSignature` is not `Valid`. Buying a certificate is an **OWNER DECISION with a cost**: list OV vs EV vs Azure Trusted Signing with current prices in the PR and stop. Keep CI-TEST naming for non-tag builds. Set `SM_CLOUD_URL=off` only for tests, **never** for the release build.
- **Acceptance tests:** pushing tag `v0.0.0-test` on a throw-away branch produces a draft release with 4 assets and the commercial name; installer file name has no `CI-TEST`; the journey passes; a `release.yml` dry-run (`workflow_dispatch`) works without secrets and prints "signing skipped".

---

## 5. HIGH

### H1 — Any cashier can refund any sale in cash without manager approval (till fraud) — reproduced
- **Where:** `server/services.py:1774` `return_sale` (approval is demanded only for `change_of_mind` on raw meat/prepared food); `:1755` `sale_detail` lets anyone with `refund` read any sale; no age limit, no owner-of-receipt check, no threshold. Probe: `cashier1` refunded `cashier2`'s sale, 4,500 piastres cash, no approval.
- **Impact:** a dishonest cashier takes cash from the drawer against old/other sales; the count still "balances" because a refund row exists.
- **Fix:** settings `refund_approval_over` (piastres, default 20000) and `refund_days_without_approval` (default 7) in `SETTING_RULES`; in `return_sale` require manager approval (the existing `approver` path, a new permission `approve_refund` for manager/owner) when `total > threshold` OR the sale is older than N business days OR the sale belongs to another cashier OR (method cash AND reason in `other`/`change_of_mind`). Audit every refund with approver id. Show the rule in Settings and in the refund dialog (Arabic).
- **Acceptance tests:** cashier alone refunding another cashier's sale -> `approval_required`; same with manager approval -> ok and audit row has approver; own small recent sale -> ok; sale older than N days -> approval; amount over threshold -> approval; wrong approver password -> rejected and counted (see M3); reconcile stays `ok`; UI test for the dialog.

### H2 — Anyone who can reach the server can lock the owner out — reproduced
- **Where:** `server/auth.py:16-17,86-101` (`MAX_FAILED=5`, `LOCK_MINUTES=5`, lock is per username and applies to the owner; `/api/login` is unauthenticated).
- **Impact:** in LAN mode (or any local user) 5 bad tries per 5 minutes keep the owner/manager permanently locked out. After 6 bad attempts the correct password returned `account_locked`.
- **Fix:** throttle by (username + client IP) and add a global per-IP limiter (HTTP 429 + `Retry-After`, exponential back-off); account lock >60 s only when failures come from the same IP; owner can always unlock from the shop PC (127.0.0.1) via the existing recovery; audit lockouts and show them on the owner's home screen.
- **Acceptance tests:** 100 bad logins from IP B never block IP A's correct login; 5 bad logins from the same IP back off; owner unlock from 127.0.0.1 works; remote unlock is refused; tests for IPv6/`X-Forwarded-For` ignored (never trust it).

### H6 — Installer grants data access to one Windows account but autostarts for all users
- **Where:** `installer/super-market.iss:95-96` (icacls: SYSTEM + Administrators + installing operator) vs `:112` autostart in `{commonstartup}` (all users).
- **Impact:** a second Windows login (e.g. the cashier's standard account) starts the EXE, cannot open `%ProgramData%\SuperMarket` and fails with "could not start".
- **Fix:** grant `Modify` to local `Users` (`*S-1-5-32-545`) **only on the data/log/backup subfolders** (not the program folder), or run the server as one background task under one account and let other users only open the window. Keep other protections (no inheritance from `C:\ProgramData`).
- **Acceptance tests:** extend `windows-portable.yml`: create a local standard user with `net user ci-cashier P@ss... /add`, start the installed EXE as that user (`Start-Process -Credential` / `runas /user`), run `tools/journey_exe.py first`; the ACL check still rejects `Everyone` and `Authenticated Users` on the data root; uninstall keeps data.

### H5 — The only off-box backup is the cloud; no USB / second-disk copy
- **Where:** `launcher.py:133-160` (`daily_backup` writes to `HOME/backups-shop`, same disk), `server/backup.py`.
- **Impact:** an offline shop (or a shop not yet connected to the cloud) loses everything if the disk fails; ransomware encrypts the same-disk backups too.
- **Fix:** setting `backup_folder_2` (any path: USB drive, second disk, network share); after each verified daily backup copy it there (`shutil.copytree`, verify the manifest checksums after copying, skip silently when the drive is missing, record last success). Show a yellow banner "no external copy for N days" on the owner's home screen; Start-menu action "نسخة على فلاشة". Keep this independent of the cloud.
- **Acceptance tests:** temp-folder tests: copy made and verified; corrupted copy detected; missing drive -> no crash and banner state; retention applied to the second folder; restore from the second folder works.

### H3 — Hardware and real-shop validation missing (SALE_READINESS gates 6 and 8) — owner task + small code
- **Where:** `docs/SALE_READINESS.md`, `docs/FIELD_TEST.md`.
- **Problem:** Arabic thermal receipt printing, keyboard-wedge scanner behaviour (speed, prefix/suffix), label-scale weighed barcodes, cash drawer are only simulated; nobody non-technical has run a full day.
- **Fix (code part only):** a one-screen "اختبار الأجهزة" page: print-test receipt (Arabic, 58 mm and 80 mm widths), scanner test (shows raw keystroke timing and detected prefix), scale-barcode test with the shop's label template; store the result in settings/audit. Add `docs/FIELD_TEST_RESULTS.md` template for the owner to fill. Do not claim hardware works.
- **Acceptance tests:** UI test of the page with simulated keystrokes; API test that results are stored and audited.

### H4 — Egyptian tax / e-receipt (ETA) compliance undefined (gate 7) — OWNER/ACCOUNTANT DECISION
- **Where:** receipts say "ليس فاتورة ضريبية"; `tax_mode` only allows `"unverified"` (`services.py` `SETTING_RULES`).
- **Impact:** a registered shop that must issue ETA e-receipts cannot use the program as its legal till.
- **Fix:** do **not** guess tax rules. Add to the sale contract and the installer EULA a plain statement: the program is an internal management system, not an ETA-compliant invoicing system. Keep receipt wording. Write `docs/ETA_QUESTIONS.md` listing exactly what the accountant must confirm (receipt numbering, VAT rates per product, e-receipt API, export). Implement only after the answers.
- **Acceptance tests:** a test that every printed receipt template contains the "not a tax invoice" statement in Arabic.

---

## 6. NEW since the cloud wiring (fix next)

### E1 — The compiled EXE is never tested against the cloud in CI
- **Where:** `windows-portable.yml` runs the `windows-installer` job with `SM_CLOUD_URL: "off"`; `tests/test_cloud_wiring.py` exercises `launcher.py` from source, not the Nuitka build.
- **Problem:** a Nuitka omission (missing stdlib module for TLS/HTTP, wrong import) would only be found by a customer.
- **Fix:** add a CI step (Windows job) that starts the dev Worker (`node cloud/dev/server.js`, `ADMIN_KEY`/`MASTER_KEY` random per run, `PORT=0`), runs the built `SuperMarket.exe` with `SM_CLOUD_URL=http://127.0.0.1:<port>`, creates a store through the admin API, runs `SuperMarket.exe cloud-join` with the code on stdin, starts the program, makes one sale through the API (`tools/journey_exe.py` helpers), waits until the dev Worker holds the rows (`/admin/stores/<id>` seq > 0 and `syncctl status complete`), then runs `SuperMarket.exe cloud-restore` into an empty `SM_HOME` and checks `reconcile.ok`. Never use the live Worker in CI.
- **Acceptance tests:** the step fails when `--include-package=server` is removed from the build; passes otherwise.

### E2 — Owner-visible "cloud copy is NOT working" banner
- **Where:** `web/js/views/admin.js` `cloudCard` (Settings only), `server/cloudsync.py` `status_light()`.
- **Problem:** if the copy silently stops (bad certificate, revoked device, clock, quota), the owner sees nothing unless he opens Settings.
- **Fix:** a red banner on the owner/manager home screen when the shop is joined and (no successful upload for 24 h OR `rejected_device` OR queue > 5000 rows). Text in formal Arabic naming the reason and the phone number/field for support. Never show addresses or secrets. Cashiers never see it.
- **Acceptance tests:** UI test with mocked `/api/cloud` states (ok, 25 h old, rejected_device, queue 6000) -> banner only in the bad states, only for owner/manager; accessibility (role=alert) and RTL layout check at 360 px width.

### E3 — Vendor onboarding is manual and the keys have a single point of failure
- **Where:** `cloud/deploy.mjs`, `cloud/wrangler.toml` (`database_id = "REPLACE-WITH-THE-ID-FROM-wrangler-d1-create"`), `tools/syncctl.py`, `docs/CLOUD_SYNC.md`.
- **Problems:** (a) the D1 `database_id` placeholder breaks `wrangler deploy` from CI on a clean checkout (it is not a secret — commit the real id once); (b) `MASTER_KEY` derives every device secret: if it is lost, every installed shop is disconnected and cannot be re-paired without re-pairing all; `ADMIN_KEY` lives only on one machine; (c) no way to delete or deactivate a store (offboarding a customer; privacy request); (d) a connection code is a bearer secret in chat.
- **Fix:** commit the real `database_id`; write `docs/KEY_RECOVERY.md` (owner stores both keys in a password manager; how to rotate `ADMIN_KEY` safely; what happens when `MASTER_KEY` rotates and how to re-pair); add admin endpoints `POST /admin/stores/<id>/deactivate` (devices get 403 `store_inactive`, data kept) and `DELETE /admin/stores/<id>` (needs `?confirm=<store name>`; deletes all rows of that store) with tests; add `syncctl admin deactivate|delete`; log admin actions in a D1 table. Pairing codes already expire in 15 min and work once — keep, and warn in the docs not to paste them in group chats.
- **Acceptance tests:** Worker tests: deactivated store -> `/v1/push` 403; delete removes every row of that store and none of another store; wrong confirm -> 400; unsigned -> 401. `syncctl` test against the dev Worker. A doc test that `wrangler.toml` has a UUID-shaped `database_id`.

### E4 — Cloud data is not encrypted by the application (privacy / sales risk)
- **Where:** `server/cloudsync.py` `export_row` (rows travel as signed JSON), D1 stores them readable; customers' names/phones/debts, sales, user names are included. `docs/CLOUD_SYNC.md` says so.
- **Impact:** anyone with access to the owner's Cloudflare account (or a Cloudflare insider) can read every customer's shop data; Egyptian data-protection law (Law 151 of 2020) may require disclosure/consent.
- **Fix (OWNER DECISION on scope; safe default now):** (1) disclose it in EULA, installer and the guide; (2) design (do not hand-roll) client-side column encryption with a vetted AEAD bundled in the build (e.g. `cryptography` AES-GCM, per-store data key wrapped by a key derived from the owner's recovery passphrase via scrypt) and write the design in `docs/CLOUD_ENCRYPTION.md` with the trade-offs (reports for phones need the key on the shop PC; Worker cannot filter encrypted columns). Implement only after the owner approves; keep the wire format versioned (`v` field) so old rows keep working.
- **Acceptance tests (when implemented):** ciphertext only in D1 (dev Worker inspection); restore with the right passphrase works; wrong passphrase -> clean error; tamper detection; mixed old/new rows restore.

### E5 — Thaw shelf life no longer defaults to 2 days for new shops (check against the owner's rule)
- **Where:** `server/app.py` (setup wizard no longer inserts `thaw_shelf_life_days='2'`; main commit "No teaching value is silently promoted"), `server/services.py:26,68-75,1612` (`None` allowed), training seed sets 2.
- **Problem:** the owner decided on 31 Oct 2026 that thawed meat keeps at most 2 days. A new real shop now has the setting empty, so thawing is either blocked or uncapped depending on the code path at `services.py:1612`.
- **Fix:** confirm with the owner (he is the authority); if the 2-day rule stands, set it as the default in the **setup wizard** with a visible, editable field and an explanatory sentence; if empty, thawing must refuse with a clear Arabic error that tells the owner where to set it — never allow uncapped thawing silently.
- **Acceptance tests:** fresh shop: thaw refused with `err.thaw_policy_missing` (or default 2 applied, per the owner's answer); with value set -> expiry = min(own expiry, now + N days); wizard UI test.

---

## 7. MEDIUM

### M1 — LAN mode is plain HTTP (passwords and session cookie visible on Wi-Fi)
- **Where:** `server/app.py` (`serve(..., lan=True)`); cookie set around `:562`, no `Secure` possible over HTTP.
- **Fix:** document "wired or isolated LAN only"; show a warning in the LAN start window and in Settings; optionally offer HTTPS with a locally generated self-signed certificate; idle-session timeout for tills (verify the screen lock covers it).
- **Acceptance tests:** warning text present in LAN mode; session expires after the configured idle time in a test.

### M2 — LAN access by computer name is rejected
- **Where:** `app.py:499` Host allow-list (`Host: shop-pc:8799` -> 421 `bad_host`).
- **Fix:** in LAN mode allow the PC's own hostname, `<hostname>.local` and private-range IPs; print the correct address(es) at startup; recommend a fixed IP in the guide. Keep DNS-rebinding protection for everything else.
- **Acceptance tests:** own hostname and private IP accepted; `evil.example` and public IP rejected (421); loopback unchanged.

### M3 — Approval-password guessing at the till
- **Where:** `server/services.py:735-755` (`_approval_failures`: 5 per 5 minutes per requesting user, **in memory**, reset on restart).
- **Impact:** a cashier can try ~1,440 manager passwords a day; a restart resets the counter.
- **Fix:** persist failures in the DB; after 5 failures lock the *requester's* approvals with exponential back-off (15 min -> 24 h); audit each failure; show it on the owner's home screen.
- **Acceptance tests:** counter survives a restart (new `Store`); lock grows; successful approval resets it; owner sees the alert.

### M4 — No socket timeout on the HTTP server (slow-connection DoS)
- **Where:** `server/app.py:515` (`rfile.read(length)`), `:590` (`daemon_threads = True`), no handler `timeout`.
- **Fix:** `Handler.timeout = 15`, bounded body read with a deadline, cap on concurrent connections (e.g. 64), cap on `Content-Length`.
- **Acceptance tests:** a client that sends headers then stalls is dropped within ~20 s while a normal request still works; oversized `Content-Length` -> 413.

### M5 — A PC clock set behind a closed business day freezes the whole shop with an unclear message
- **Where:** `server/db.py:115` `business_day_for` (`today() < closed` -> `day_closed` for everyone).
- **Fix:** detect at start-up and show a dedicated Arabic screen "ساعة الجهاز خاطئة — صحّحها (التاريخ الصحيح يبدأ من …)" naming the closed date; never auto-post into the past.
- **Acceptance tests:** clock behind closed day -> message names the closed date; after correcting the clock selling works.

### M6 — Local backup ordering depends on the PC clock
- **Where:** `server/backup.py:29,85-87` (folder name `%Y%m%d-%H%M%S` is the sort key).
- **Fix:** order by a monotonic `seq` stored in each manifest (max `ops.id` + counter), not by folder name; a wrong clock must not prune the newest copy.
- **Acceptance tests:** create backups with clock jumping backwards; newest by `seq` is kept and restored.

### M7 — Local backup retention only prunes `daily`; no disk-space check
- **Where:** `launcher.py:47,146` (`KEEP_DAILY = 30`); `before-<version>` and manual copies are never pruned.
- **Fix:** keep the last N `before-*` (e.g. 5); warn when free disk < 2 GB or backups exceed a configurable size; on failure log and surface a banner instead of failing silently.
- **Acceptance tests:** retention counts per type; low-disk simulation produces the warning, not an exception.

### M8 — D1 free-tier quota claims are optimistic
- **Where:** `docs/CLOUD_SYNC.md` quota estimate; `cloud/schema.sql` (secondary indexes multiply row writes).
- **Fix:** recompute writes/day = rows x (1 + index count) and state how many shops/day fit in 100k writes; coalesce repeated changes of the same row within a batch (client already keys by row); throttle the first full copy of a big database across days; surface "quota exhausted" (Cloudflare 429/D1 limit error) to the owner through `error_kind` and the E2 banner.
- **Acceptance tests:** unit test of the batching/coalescing; mocked D1-limit error -> status `quota` and Arabic text.

### M9 — Pairing/admin throttles can be used to deny service
- **Where:** `cloud/src/worker.js:~110` (`rate` table keyed by `key`/window), pairing and admin counters.
- **Fix:** key throttles by `CF-Connecting-IP` and by store; admin lockout only for the offending IP after 10 failures; add a Cloudflare WAF rate-limit rule note for `/admin/*` and `/v1/pair` in the docs.
- **Acceptance tests:** Worker tests: failures from IP B don't throttle IP A; correct admin key from IP A works while B is blocked.

### M10 — Generic `ValueError -> 400` hides real bugs
- **Where:** `server/app.py:332,481,518` (broad `except ValueError`).
- **Fix:** map only `ShopError` and `json.JSONDecodeError` to 400; any other exception -> 500 with a logged traceback and a request id (no stack to the client). Keep Arabic generic message.
- **Acceptance tests:** an injected `ValueError` inside a service returns 500 and logs a traceback; malformed JSON still returns 400.

---

## 8. LOW

- **L1** `server/domain.py` is dead code (only `tests/test_domain.py` uses it). Delete it and the test, or wire it; update `CLAUDE.md`. *Test:* grep shows no import of `domain` outside tests, suite passes.
- **L2** Promotions use calendar `today()` instead of the business day (`server/services.py:808` `_promotion_for(prod["id"], today())`). A promotion ending today stops at midnight though the shop's day runs to `day_start_hour`. Use the business day. *Test:* with `day_start_hour=4`, a promo ending yesterday still applies at 02:00 today.
- **L3** A plain `http://` request to the live Worker returns 500 (should be 400 or redirect). Fix in `cloud/src/worker.js`; enable "Always Use HTTPS". *Test:* Worker test with `http://` URL -> 400/308.
- **L4** Pre-upgrade copy folder `before-<version>` has no seconds: two upgrades in one minute collide (`launcher.py:140`, `server/db.py` pre-upgrade copy). Include seconds. *Test:* two copies within the same minute both exist.
- **L5** `GET /api/state` is unauthenticated and returns shop name + instance id (`server/app.py:41,57`). Return only `version/mode/setup_needed` unless the caller is 127.0.0.1 or logged in (the launcher needs the rest locally). *Test:* remote Host/IP gets the short form.
- **L6** PBKDF2 iterations 240,000 (`server/auth.py:14`) < OWASP 2023 600,000 for PBKDF2-SHA256. Raise to >= 600,000 with rehash-on-login (verify the stored iteration field is honoured; add if missing). *Test:* an old hash verifies and is upgraded after login; new hashes use the new count.
- **L7** Add a test that every `ShopError("code")` string in `server/*.py` has `err.<code>` in both `ar.js` and `en.js`, and every `err.*` key is used (allow-list for dynamic codes). The four cloud/restore codes were added already; this test keeps it from regressing. *Test:* the test fails when a code is removed from one dictionary.
- **L8** `add_party` has no duplicate check (a double click creates two customers) and does not validate the phone (`server/services.py:209`). Normalize Egyptian mobile numbers (`01[0125]xxxxxxxx`, accept `+20`), warn on same name+phone. *Test:* duplicate refused with a clear error; `+201012345678` normalizes to `01012345678`.
- **L9** `python -m unittest discover -s tests -t .` fails (tests is not a package); only `tools/ci_tests.py` works. Document it in `README.md` and `CLAUDE.md` and make `python3 -m unittest discover -s tests` work (add `tests/__init__.py` only if it does not break `from helpers import ...`). *Test:* both commands pass in CI.
- **L10** Closing the business day early pushes the rest of the day's sales into tomorrow and there is no "reopen". Add an Arabic confirmation stating this, and an owner-only audited "reopen last day" when no later day has data. *Test:* reopen works only for the owner and only when the next day is empty.
- **L11** Return restock puts the whole quantity into the first allocation's location and a single lot with the earliest expiry (`server/services.py` `return_sale`, restock branch) even when the sale drew from several lots/locations. Safe but loses precision. Split per allocation proportionally. *Test:* a sale drawn from two lots, partially returned, restocks to the same two lots in proportion; reconcile ok.
- **L12** The program window uses `msedge --app` with the user's own browser profile (`server/window.py` `app_command`). Password-manager prompts/extensions/sync can appear in the till window. Use a dedicated `--user-data-dir=%ProgramData%\SuperMarket\window-profile` (still no first-run pages). *Test:* `app_command` contains the dedicated profile directory; journey still opens the UI.
- **L13** Cloud restore leaves an extra folder `shop-data-empty-<timestamp>` aside on a new PC (by design: nothing is deleted). Add a Start-menu hint and delete it automatically only when it is provably empty (no users, no ops). *Test:* empty aside folder is removed after a successful restore; a non-empty one is kept.

---

## 9. Definition of done

1. All sections above implemented or, for OWNER DECISION items, implemented as far as safe with the question written in the PR description.
2. `python3 tools/ci_tests.py`, `cd cloud && npm test`, browser UI scripts, `windows-portable`, `cloud-sync` and `browser-review` workflows are green on the PR.
3. `docs/SALE_READINESS.md` gates updated honestly (a gate is green only with evidence); `DEVELOPMENT_HISTORY.md` has one entry with the bugs you found and the lessons.
4. No secret anywhere in the diff (`git diff origin/main | grep -Ei "ghp_|github_pat_|AKIA|BEGIN .*PRIVATE KEY|ADMIN_KEY=|MASTER_KEY="` is empty).
5. Final message to the owner in simple Arabic: what now works, what is still his decision (licence/repo privacy, certificate cost, tax rules, encryption), and the exact next step for him. Do not merge; wait for «انشر».
