"""JavaScript for the DOM privacy detector (raw templates and the builder). Re-exported by privacy.

Contract for callers of DOM_SCAN_JS (shoot / capture.web, BrowserOS neo):

- The result is a plain list of findings and is ALWAYS complete: every text node is read
  (in chunks of `opts.chunk` chars, default 1,000,000, overlapping so a match on a chunk
  boundary is still found) and there is no cap on the number of findings. There is no
  `truncated` flag to check; nothing is silently dropped.
- `find` mirrors privacy.find_in_text: allowed values and unwanted kinds are dropped first,
  then overlapping matches are UNIONED (merged span, most severe kind from SEVERITY).
- Form fields (`scanFields`): a password input with a value is always redacted; any other
  field whose whole value is allow-listed is left visible. `name_field` / `address_field`
  come only from this field classification (type, autocomplete, name/id/label hints).
"""
from __future__ import annotations

import json

_JS = r"""(opts) => {
  opts = opts || {};
  const PAT = (opts.patterns || []).map(s => ['custom', 'any', '', s]).concat(__PATTERNS__);
  const RX = PAT.map(p => [p[0], p[1], new RegExp(p[3], 'gd' + p[2])]);
  const allow = (opts.allow || []).map(a => a.toLowerCase());
  const kinds = opts.kinds ? new Set(opts.kinds) : null;
  const D = s => s.replace(/\D/g, '');
  const ent = s => { const c = {}; for (const ch of s) c[ch] = (c[ch] || 0) + 1;
    return -Object.values(c).reduce((a, n) => a + n / s.length * Math.log2(n / s.length), 0); };
  const cls = ch => /\d/.test(ch) ? 'd' : /[A-Z]/.test(ch) ? 'u' : /[a-z]/.test(ch) ? 'l' : '';
  const KEYCTX = /(key|token|secret|auth|signature|password|session|bearer|cookie)\W{0,12}[\w ]{0,20}$/i;
  const STOP = new Set(__STOP__), FTLD = new Set(__FTLD__), SEV = __SEV__;
  const sev = k => { const i = SEV.indexOf(k); return i < 0 ? SEV.length : i; };
  const TRUNC = /\u2026|\.{2,}/;
  const V = {
    any: (t, ms, s, e, v, k) => k,
    jwt: (t, ms, s, e, v, k) => { const [h, p] = v.split('.'); let j = '';
      try { j = atob(h.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - h.length % 4) % 4)); } catch (x) {}
      return (j.includes('"alg"') || j.includes('"typ"') || p.startsWith('eyJ')) ? k : null; },
    bearer: (t, ms, s, e, v, k) => /\d/.test(v) && /[A-Za-z]/.test(v) ? k : null,
    kv: (t, ms, s, e, v, k) => {
      if (STOP.has(v.toLowerCase().replace(/^\.+|\.+$/g, '')) || /^[*•●·.xX]+$/.test(v) || /^[<{$\[(]/.test(v)) return null;
      return /^(pass|pwd)/i.test(t.slice(ms, s)) ? 'password_field' : k; },
    email: (t, ms, s, e, v, k) => FTLD.has(v.split('.').pop().toLowerCase()) ? null : k,
    card: (t, ms, s, e, v, k) => { const d = D(v), g = v.split(/[ -]/);
      if (d.length < 13 || d.length > 19 || !'23456'.includes(d[0]) || new Set(d).size < 2) return null;
      if (g.length > 1 && (new Set(v.match(/[ -]/g)).size > 1 || Math.min(...g.map(x => x.length)) < 3)) return null;
      let t2 = 0; [...d].reverse().forEach((ch, i) => { let x = +ch * (i % 2 ? 2 : 1); t2 += x > 9 ? x - 9 : x; });
      return t2 % 10 === 0 ? k : null; },
    iban: (t, ms, s, e, v, k) => { const c = v.replace(/ /g, ''); if (c.length < 15 || c.length > 34) return null;
      let r = 0; for (const ch of c.slice(4) + c.slice(0, 4)) for (const d of String(parseInt(ch, 36))) r = (r * 10 + +d) % 97;
      return r === 1 ? k : null; },
    phone: (t, ms, s, e, v, k) => { const d = D(v);
      if (d.length < 7 || d.length > 15 || new Set(d).size < 3 || /^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$/.test(v.trim())) return null;
      return /(order|invoice|ref|sku|tracking|id|#|no\.?|nr\.?)\s*[:#]?\s*$/i.test(t.slice(Math.max(0, ms - 16), ms)) ? null : k; },
    ip: (t, ms, s, e, v, k) => (v === '0.0.0.0' || v === '127.0.0.1' || v.startsWith('255.') ||
      /\b(version|ver\.?|v)\s*$/i.test(t.slice(Math.max(0, s - 9), s))) ? null : k,
    entropy: (t, ms, s, e, v, k) => { const c = v.replace(/=+$/, '');
      if (!/[A-Za-z]/.test(c) || !/\d/.test(c)) return null;
      if (/^[0-9a-fA-F-]+$/.test(c)) return KEYCTX.test(t.slice(Math.max(0, s - 40), s)) ? k : null;
      const wordy = (c.match(/[A-Z]?[a-z]{3,}|[A-Z]{3,}/g) || []).reduce((a, x) => a + x.length, 0);
      if (wordy > 0.5 * c.length || ent(c) < 3.5) return null;
      const cl = [...c].map(cls).filter(Boolean); let sw = 0;
      for (let i = 1; i < cl.length; i++) sw += cl[i] !== cl[i - 1];
      return sw / Math.max(1, cl.length - 1) >= 0.3 ? k : null; },
  };
  const PREFIX = /^(sk-(?:proj-|ant-)?|[sprk]k_(?:live|test)_|gh[pousr]_|github_pat_|xox[a-z]-|AKIA|ASIA|AIza|glpat-|hf_|npm_|shp[a-z]{2}_|SG\.|xai-|eyJ)/;
  const mask = (value, kind) => { const v = value.trim(), d = D(v);
    if (kind === 'email' && v.includes('@')) { const l = v.slice(0, v.indexOf('@')), dom = v.slice(v.indexOf('@') + 1), i = dom.lastIndexOf('.');
      if (TRUNC.test(v) || i < 0) return `${l.slice(0, 1)}***@${/^[A-Za-z0-9]/.test(dom) ? dom[0] : ''}***\u2026`;
      return `${l.slice(0, 1)}***@${dom.slice(0, 1)}***.${dom.slice(i + 1)}`; }
    if (['api_key', 'token', 'jwt', 'custom'].includes(kind)) { const p = v.match(PREFIX);
      return `${p ? p[1] : v.length >= 12 ? v.slice(0, 2) : ''}***(${v.length} chars)`; }
    if (kind === 'credit_card') return `**** ${d.slice(-4)}`;
    if (kind === 'iban') return `${v.slice(0, 2)}** ****${v.replace(/ /g, '').slice(-2)}`;
    if (kind === 'phone') return `${v.startsWith('+') ? '+' : ''}*** **${d.slice(-2)}`;
    if (kind === 'ip') return v.split('.')[0] + '.***.***.***';
    return (kind === 'password_field' || v.length < 4) ? '***' : v.slice(0, 1) + '***'; };
  const esc = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const allowed = v => { const l = v.trim().toLowerCase();
    if (allow.some(a => l === a || (a[0] === '@' && l.endsWith(a)))) return true;
    if (!TRUNC.test(l)) return false;
    const rx = new RegExp('^' + l.split(/\u2026|\.{2,}/).map(esc).join('[^]*') + '$');
    return allow.some(a => a[0] !== '@' && rx.test(a)); };
  function find(text) {
    text = text.replace(/\u00a0/g, ' ');
    const c = [];
    RX.forEach(([kind, vn, re]) => { re.lastIndex = 0; let m;
      while ((m = re.exec(text))) {
        if (!m[0]) { re.lastIndex++; continue; }
        const [s, e] = (m.length > 1 && m[1] !== undefined) ? m.indices[1] : m.indices[0];
        const k = e > s ? V[vn](text, m.index, s, e, text.slice(s, e), kind) : null;
        if (k && (!kinds || kinds.has(k)) && !allowed(text.slice(s, e))) c.push([s, e, k]);
      } });
    c.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const out = [];      // union: overlapping matches merge, the most severe kind wins
    for (const [s, e, k] of c) { const l = out[out.length - 1];
      if (l && s < l[2]) { if (sev(k) < sev(l[0])) l[0] = k; l[2] = Math.max(l[2], e); } else out.push([k, s, e]); }
    return out;
  }
  if (typeof opts.text === 'string') return find(opts.text).map(([kind, s, e]) => ({kind, start: s, end: e, preview: mask(opts.text.slice(s, e), kind)}));
__DOM__
}"""

_DOM = r"""
  const full = !!opts.fullPage, sx = full ? scrollX : 0, sy = full ? scrollY : 0;
  const off = opts.offset || [0, 0], top = opts.clip || (full ? [-1e9, -1e9, 1e9, 1e9] : [0, 0, innerWidth, innerHeight]);
  const out = [], seen = new Set();   // no cap: every finding is returned
  const cs = el => el.ownerDocument.defaultView.getComputedStyle(el);
  const vis = el => el.checkVisibility ? el.checkVisibility({opacityProperty: true, visibilityProperty: true})
    : (cs(el).display !== 'none' && cs(el).visibility !== 'hidden' && +cs(el).opacity > 0);
  function add(kind, r, ox, oy, clip, preview, extra) {
    const x1 = Math.max(r.left + ox, clip[0]), y1 = Math.max(r.top + oy, clip[1]);
    const x2 = Math.min(r.right + ox, clip[2]), y2 = Math.min(r.bottom + oy, clip[3]);
    if (x2 - x1 < 2 || y2 - y1 < 2) return;
    const f = {kind, x: +(x1 + sx).toFixed(1), y: +(y1 + sy).toFixed(1), w: +(x2 - x1).toFixed(1), h: +(y2 - y1).toFixed(1),
      units: 'css', source: 'dom', preview, ...(extra || {})};
    const key = [kind, f.x, f.y, f.w, f.h].join();
    if (!seen.has(key) && (!kinds || kinds.has(kind))) { seen.add(key); out.push(f); }
  }
  const HINTS = [['password_field', /pass(word|wd|code|phrase)?|pwd|\bpin\b|\botp\b|one.?time|\b2fa\b|\bmfa\b|totp/],
    ['credit_card', /\bcc-|card|cvv|cvc|\bcsc\b|expir|exp.?date/], ['iban', /iban|\bbic\b|swift|routing|account.?(number|no)|sort.?code/],
    ['api_key', /api.?key|apikey|access.?key|client.?secret|private.?key|secret.?key/], ['token', /token|secret|bearer|auth.?code/],
    ['email', /e.?mail/], ['phone', /phone|mobile|\btel\b|whatsapp|\bfax\b/], ['ip', /\bip\b|ip.?addr/],
    ['address_field', /address|street|\baddr|\bzip\b|postal|postcode|\bcity\b/],
    ['name_field', /(first|last|full|given|family|middle|sur|user|customer|contact|billing|shipping|display|nick).?name|username|\blogin\b/]];
  const AC = [['password_field', /password|one-time-code/], ['credit_card', /^cc-/], ['email', /email/], ['phone', /^tel/],
    ['address_field', /address|postal-code/], ['name_field', /name$/]];
  const SKIPTYPE = /^(hidden|checkbox|radio|submit|button|reset|image|file|range|color)$/;
  function fieldKind(el) {
    const t = (el.type || '').toLowerCase(), ac = (el.autocomplete || el.getAttribute('autocomplete') || '').toLowerCase();
    if (t === 'password') return 'password_field';
    if (t === 'email') return 'email';
    if (t === 'tel') return 'phone';
    for (const tok of ac.split(/\s+/)) for (const [k, re] of AC) if (re.test(tok)) return k;
    const lab = el.labels ? [...el.labels].map(l => l.textContent).join(' ') : '';
    const h = [el.name, el.id, el.getAttribute('aria-label'), el.placeholder, lab].filter(Boolean).join(' ')
      .replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
    for (const [k, re] of HINTS) if (re.test(h)) return k;
    return null;
  }
  function scanFields(root, ox, oy, clip) {
    for (const el of root.querySelectorAll('input, textarea, select')) {
      if (SKIPTYPE.test(el.type || '') || !vis(el)) continue;
      const r = el.getBoundingClientRect();
      const val = el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).text || '' : el.value || '';
      let k = fieldKind(el);
      if (k === 'password_field') { if (val.length > 0) add(k, r, ox, oy, clip, '***', {field: el.name || el.id || ''}); continue; }
      if (!val.trim() || allowed(val)) continue;      // allow list applies to classified fields too
      if (!k) { const f = find(val)[0]; if (f) k = f[0]; }
      if (k) add(k, r, ox, oy, clip, mask(val, k), {field: el.name || el.id || ''});
    }
  }
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'TEXTAREA', 'OPTION', 'SELECT']);
  const blocks = new Map();
  const blockOf = el => { const chain = []; let e = el, b = null;
    while (e) { if (blocks.has(e)) { b = blocks.get(e); break; } chain.push(e);
      const d = cs(e).display; if (!d.startsWith('inline') && d !== 'contents') { b = e; break; } e = e.parentElement; }
    chain.forEach(c => blocks.set(c, b)); return b; };
  function lineRects(rects) {
    const lines = [];
    for (const r of rects) { if (r.width < 1 || r.height < 1) continue;
      const l = lines.find(l => Math.abs((l.top + l.bottom) / 2 - (r.top + r.bottom) / 2) < Math.min(l.bottom - l.top, r.height) / 2);
      if (l) { l.left = Math.min(l.left, r.left); l.right = Math.max(l.right, r.right); l.top = Math.min(l.top, r.top); l.bottom = Math.max(l.bottom, r.bottom); }
      else lines.push({left: r.left, right: r.right, top: r.top, bottom: r.bottom}); }
    return lines;
  }
  const CH = opts.chunk || 1e6, OV = Math.min(65536, CH >> 1), CTX = 256;
  function scanText(root, doc, ox, oy, clip) {
    // Every text node is read. Past CH chars the text is scanned and all but its last OV chars
    // reported; the tail (plus CTX chars of left context for key=value / keyword checks) is
    // kept for the next chunk, so a match across the boundary is found whole. A map offset may
    // be negative: the chunk then starts inside that node.
    const tw = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: n =>
      (n.parentElement && !SKIP.has(n.parentElement.tagName) && n.nodeValue.trim()) ? 1 : 2});
    let text = '', prev = undefined, n, map = [];
    const at = (pos, end) => { let lo = 0, hi = map.length - 1;
      while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (map[mid][0] <= pos - (end ? 1 : 0)) lo = mid; else hi = mid - 1; }
      const [o, node] = map[lo]; return [node, Math.max(0, Math.min(node.nodeValue.length, pos - o))]; };
    const report = lim => { for (const [kind, s, e] of find(text)) {
      if (s >= lim) continue;
      const [sn, so] = at(s, false), [en, eo] = at(e, true);
      if (!vis(sn.parentElement) || !vis(en.parentElement)) continue;
      const rg = doc.createRange(); rg.setStart(sn, so); rg.setEnd(en, eo);
      const p = mask(text.slice(s, e), kind);
      for (const r of lineRects(rg.getClientRects())) add(kind, r, ox, oy, clip, p);
    } };
    while ((n = tw.nextNode())) {
      const b = blockOf(n.parentElement);
      if (text && b !== prev) text += '\n';
      prev = b; map.push([text.length, n]); text += n.nodeValue;
      if (text.length >= CH) {
        const lim = text.length - OV, cut = Math.max(0, lim - CTX);
        report(lim);
        let i = 0; while (i + 1 < map.length && map[i + 1][0] <= cut) i++;
        map = map.slice(i).map(([o, nd]) => [o - cut, nd]); text = text.slice(cut);
      }
    }
    if (text) report(Infinity);
  }
  const PAY = /stripe\.com|braintree|adyen|paypal\.com|checkout\.com|recurly|squareup|chargebee|authorize\.net|worldpay|klarna|hcaptcha-pay/;
  function scanDoc(doc, ox, oy, clip, depth) {
    const roots = [doc];
    for (let i = 0; i < roots.length; i++)
      for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
    for (const root of roots) { scanFields(root, ox, oy, clip); scanText(root === doc ? doc.body || doc.documentElement : root, doc, ox, oy, clip); }
    for (const f of doc.querySelectorAll('iframe, frame')) {
      if (!vis(f)) continue;
      const r = f.getBoundingClientRect(), s = cs(f);
      const cx = ox + r.left + f.clientLeft + parseFloat(s.paddingLeft), cy = oy + r.top + f.clientTop + parseFloat(s.paddingTop);
      const inner = [Math.max(cx, clip[0]), Math.max(cy, clip[1]),
        Math.min(cx + f.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight), clip[2]),
        Math.min(cy + f.clientHeight - parseFloat(s.paddingTop) - parseFloat(s.paddingBottom), clip[3])];
      let cd = null; try { cd = f.contentDocument; } catch (x) {}
      if (cd && cd.documentElement && depth < 6) { if (inner[2] > inner[0] && inner[3] > inner[1]) scanDoc(cd, cx, cy, inner, depth + 1); continue; }
      let host = ''; try { host = new URL(f.src, location.href).host; } catch (x) {}
      add(PAY.test(host) ? 'credit_card' : 'unknown_frame', r, ox, oy, clip, host || 'iframe', {frame: f.src || ''});
    }
  }
  scanDoc(document, off[0], off[1], top, 0);
  return out;
"""


def build_dom_scan_js(patterns, kv_stop, file_tld, severity):
    """DOM_SCAN_JS: the scanner template with the shared regexes, stop-lists and the kind
    severity order (for unioned overlaps) inlined as JSON."""
    return (_JS.replace("__PATTERNS__", json.dumps([list(p) for p in patterns]))
            .replace("__STOP__", json.dumps(sorted(kv_stop))).replace("__FTLD__", json.dumps(sorted(file_tld)))
            .replace("__SEV__", json.dumps(list(severity))).replace("__DOM__", _DOM))


_CONTENT_BOX = """e => { const s = getComputedStyle(e), n = k => parseFloat(s[k]) || 0;
  return [e.clientLeft + n('paddingLeft'), e.clientTop + n('paddingTop'),
          e.clientWidth - n('paddingLeft') - n('paddingRight'), e.clientHeight - n('paddingTop') - n('paddingBottom')]; }"""
