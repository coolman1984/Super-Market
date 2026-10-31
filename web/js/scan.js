/* Scanner input in the browser.
   1) Keyboard-wedge scanners (USB HID keyboard, 2.4 GHz dongle, Bluetooth HID)
      type into the dedicated scan field and end with Enter/Tab.
   2) If the cashier's profile has a prefix character, a scan that starts with
      it is captured even when focus is elsewhere, and never reaches that field.
   3) Serial/TCP scanners arrive from the shop server as events with ids.
   Scans are ignored (with a notice) while a dialog such as payment or manager
   approval is open: a scan can never pay, approve or edit an amount.
   Typing speed is NOT used to tell a scanner from a person. */
'use strict';

SM.scanner = {
  profile: {},
  handler: null,
  lastEvent: 0,
  polling: false,

  async load() {
    try { this.profile = await SM.api('GET', '/api/scanner/profile'); } catch (e) { this.profile = {}; }
    this.startEvents();
  },

  suffixKey() { return this.profile.suffix === 'TAB' ? 'Tab' : 'Enter'; },

  deliver(raw, source) {
    if (!raw) return;
    if (SM.state.dialogOpen && source !== 'dialog') { SM.toast(SM.t('scan.ignored_dialog'), 'bad'); return; }
    if (!this.handler) { SM.toast(SM.t('scan.no_target'), 'bad'); return; }
    this.handler(raw, source);
  },

  /* Bind a visible scan field. */
  field(opts = {}) {
    const input = SM.h('input', { class: 'input scan-input', type: 'text', inputmode: 'text', autocomplete: 'off', spellcheck: 'false',
      'aria-label': opts.label || SM.t('scan.field'), placeholder: opts.placeholder || SM.t('scan.placeholder'), maxlength: '600' });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === this.suffixKey() || ev.key === 'Enter') {
        ev.preventDefault();
        const raw = input.value;
        input.value = '';
        if (raw.trim()) (opts.onEnter || ((r) => this.deliver(r, 'field')))(raw);
      }
    });
    return input;
  },

  /* Global prefix capture (only when the profile defines a prefix). */
  installGlobal() {
    let capturing = false, buffer = '', timer = null;
    document.addEventListener('keydown', (ev) => {
      const prefix = this.profile.prefix;
      if (!prefix || prefix.length !== 1) return;
      if (!capturing && ev.key === prefix && !ev.ctrlKey && !ev.altKey) {
        capturing = true; buffer = ''; ev.preventDefault(); ev.stopPropagation();
        clearTimeout(timer); timer = setTimeout(() => { capturing = false; buffer = ''; }, 2000);
        return;
      }
      if (!capturing) return;
      ev.preventDefault(); ev.stopPropagation();
      if (ev.key === 'Enter' || ev.key === 'Tab') {
        capturing = false; clearTimeout(timer);
        this.deliver(buffer, 'prefix');
        buffer = '';
      } else if (ev.key.length === 1) {
        buffer += ev.key;
      }
    }, true);
  },

  /* Serial / TCP scanners through the shop server. The server binds each
     device to one till; this window claims the till's lease (audit R09), and
     follows the server's epoch so a restart never freezes reading (R13). */
  window_id: null,
  epoch: null,
  async startEvents() {
    if (this.polling || !['serial', 'tcp'].includes(this.profile.connection)) return;
    this.polling = true;
    const session = SM.state.session;
    this.window_id = this.window_id || SM.newKey();
    try {
      const c = await SM.api('POST', '/api/scanner/claim', { window: this.window_id });
      this.epoch = c.epoch; this.lastEvent = c.last;
    } catch (e) { this.polling = false; return; }
    while (this.polling && SM.state.user && session === SM.state.session) {
      try {
        const r = await SM.api('GET', '/api/scanner/events?wait=15&window=' + this.window_id + '&after=' + this.lastEvent +
          '&epoch=' + encodeURIComponent(this.epoch || ''));
        if (session !== SM.state.session) break;            // a reply for a user who has left
        SM.state.devices = r.devices;
        if (r.restarted) SM.toast(SM.t('scan.server_restarted'), 'bad');
        if (r.missed) SM.toast(SM.t('scan.missed', { n: r.missed }), 'bad');
        this.epoch = r.epoch;
        for (const ev of r.events) {
          if (ev.id <= this.lastEvent && !r.restarted) continue;   // same event twice -> once
          this.lastEvent = ev.id;
          this.deliver(ev.text, 'device');
        }
        if (!r.events.length) this.lastEvent = r.last;
      } catch (e) {
        if (e.code === 'scanner_taken_by_other_window') {
          this.polling = false;
          SM.toast(SM.t('scan.taken_elsewhere'), 'bad');
          break;
        }
        await new Promise(r => setTimeout(r, 3000));
      }
    }
    this.polling = false;
  },
  /* "Use the scanner here": take the lease back for this window. */
  async takeOver() { this.polling = false; await new Promise(r => setTimeout(r, 50)); this.startEvents(); },
  stop() { this.polling = false; },

  /* Feedback: text always; sound only if the cashier enabled it. */
  beep(ok) {
    if (!this.profile.beep) return;
    try {
      const ctx = this._ctx || (this._ctx = new (window.AudioContext || window.webkitAudioContext)());
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = ok ? 1200 : 300; g.gain.value = .08;
      o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + (ok ? .08 : .25));
    } catch (e) { /* no audio device */ }
  },
};
