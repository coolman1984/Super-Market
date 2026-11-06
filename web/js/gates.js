/* Phone and thaw gates shared by the screens. No shop setting: a phone browser
   is detected from the user agent, and thaw stays hidden until settings say it is on. */
(function (root) {
  function isPhoneDevice(ua) {
    return /iPhone|iPod|Windows Phone|IEMobile|Opera Mini|Android.+Mobile/i.test(String(ua || ''));
  }

  function phoneWorkstation(stored, phone, freshId) {
    const id = String(freshId || 'x').replace(/[^A-Za-z0-9]/g, '').slice(0, 12) || 'x';
    if (!stored) return (phone ? 'phone-' : 'till-') + id;
    if (phone && !/^phone-/i.test(stored)) {
      const suffix = String(stored).replace(/^(?:till-|phone-)/i, '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40) || id;
      return 'phone-' + suffix;
    }
    return stored;
  }

  /* Drop the thaw lesson while the owner has not turned thaw on. Other lessons stay. */
  function visibleCatalogue(cat, opts) {
    const thaw = !!(opts && opts.thaw);
    if (!cat || thaw) return cat;
    const copy = JSON.parse(JSON.stringify(cat));
    copy.problems = (copy.problems || []).filter(function (p) { return p.id !== 'freezer-and-places'; });
    return copy;
  }

  const api = { isPhoneDevice: isPhoneDevice, phoneWorkstation: phoneWorkstation, visibleCatalogue: visibleCatalogue };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root && root.SM) {
    root.SM.isPhoneDevice = isPhoneDevice;
    root.SM.phoneWorkstation = phoneWorkstation;
    root.SM.visibleCatalogue = visibleCatalogue;
    root.SM.isPhone = function () { return isPhoneDevice((root.navigator && root.navigator.userAgent) || ''); };
    root.SM.thawOn = function () { return !!(root.SM.state && root.SM.state.settings && root.SM.state.settings.thaw_enabled); };
  }
})(typeof window !== 'undefined' ? window : globalThis);
