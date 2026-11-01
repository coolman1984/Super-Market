'use strict';
/* Explicit waits for the UI suite. A fixed sleep passes on a quiet machine and
   fails when several suites share the CPU. Wait for the response or the screen. */

async function scanCode(page, code) {
  const pending = page.waitForResponse(
    (r) => r.url().includes('/api/scan') && r.request().method() === 'POST',
    { timeout: 15000 });
  const before = await page.evaluate(() => ({
    rows: document.querySelectorAll('.cart tbody tr').length,
    status: (document.querySelector('.scan-status') || {}).textContent || '',
    qty: (document.querySelector('.cart .qty-cell') || {}).textContent || '',
  }));
  await page.fill('.scan-input', code);
  await page.press('.scan-input', 'Enter');
  await pending;
  await page.waitForFunction((prev) => {
    const rows = document.querySelectorAll('.cart tbody tr').length;
    const status = (document.querySelector('.scan-status') || {}).textContent || '';
    const qty = (document.querySelector('.cart .qty-cell') || {}).textContent || '';
    return rows !== prev.rows || status !== prev.status || qty !== prev.qty;
  }, before, { timeout: 15000 });
}

async function viewText(page, hash) {
  await page.waitForFunction((hash) => {
    const view = document.querySelector('#view');
    return location.hash === hash && view && view.innerText.trim().length > 0;
  }, hash, { timeout: 15000 });
}

module.exports = { scanCode, viewText };
