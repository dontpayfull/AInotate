"""Page-side JavaScript snippets evaluated by Playwright / CDP (split out of web.py)."""
from __future__ import annotations

_MEASURE = """function(el){
  const r = el.getBoundingClientRect();
  const o = {x: r.x, y: r.y, w: r.width, h: r.height, visible: true, reason: '', fixed: false, inner: false};
  if (r.width < 1 || r.height < 1) { o.visible = false; o.reason = 'zero size'; return o; }
  const cv = el.checkVisibility ? !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
    : (getComputedStyle(el).visibility === 'hidden');
  if (cv) { o.visible = false; o.reason = 'hidden by CSS'; return o; }
  let l = r.left, t = r.top, rr = r.right, b = r.bottom;
  for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
    const s = getComputedStyle(a);
    if (s.position === 'fixed') o.fixed = true;
    if (a === el || a === document.body) continue;
    const ov = s.overflowX + ' ' + s.overflowY;
    if (ov === 'visible visible') continue;
    if (/auto|scroll/.test(ov) && (a.scrollHeight > a.clientHeight + 1 || a.scrollWidth > a.clientWidth + 1)) {
      o.inner = true; continue; }
    const ar = a.getBoundingClientRect();
    l = Math.max(l, ar.left); t = Math.max(t, ar.top); rr = Math.min(rr, ar.right); b = Math.min(b, ar.bottom);
    if (rr - l < 1 || b - t < 1) { o.visible = false; o.reason = 'clipped by an overflow:hidden ancestor'; return o; }
  }
  if (!o.inner) { o.x = l; o.y = t; o.w = rr - l; o.h = b - t; }
  return o;
}"""
_MEASURE_ALL = f"els => els.map({_MEASURE})"
_MEASURE_ONE = f"el => ({_MEASURE})(el)"
_FRAME_BOX = """f => { const r = f.getBoundingClientRect(), s = getComputedStyle(f), p = k => parseFloat(s[k]) || 0;
  return {x: r.x, y: r.y, cl: f.clientLeft, ct: f.clientTop, cw: f.clientWidth, ch: f.clientHeight,
    pl: p('paddingLeft'), pt: p('paddingTop'), pr: p('paddingRight'), pb: p('paddingBottom'),
    visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'}; }"""
_STATE = """() => ({sx: scrollX, sy: scrollY, vw: innerWidth, vh: innerHeight, dpr: devicePixelRatio,
  dw: Math.max(document.documentElement.scrollWidth, innerWidth),
  dh: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)})"""
_TOP_INSET = """() => { let inset = 0;
  for (const fx of [0.1, 0.5, 0.9]) {
    for (let e = document.elementFromPoint(innerWidth * fx, 2); e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.position === 'fixed' || s.position === 'sticky') {
        const r = e.getBoundingClientRect();
        if (r.top <= 2 && r.bottom < innerHeight * 0.4) inset = Math.max(inset, r.bottom);
        break; } } }
  return inset; }"""
_HIDE_OVERLAYS = """() => { const hidden = window.__ainotateHidden || (window.__ainotateHidden = []); let n = 0;
  for (const e of document.querySelectorAll('body *')) {
    const s = getComputedStyle(e);
    // only floating layers: a page wrapper with class "has-consent" must stay visible
    if (!(s.position === 'fixed' || s.position === 'sticky' || e.tagName === 'IFRAME') || s.display === 'none') continue;
    const id = (e.id + ' ' + (e.getAttribute('class') || '')).toLowerCase(), tx = e.textContent || '';
    if (/cmp|consent|cookie|onetrust|didomi|truste/.test(id) || (/cookie|consent|privacy/i.test(tx) && tx.length < 800)) {
      hidden.push([e, e.style.getPropertyValue('display'), e.style.getPropertyPriority('display')]);
      e.style.setProperty('display', 'none', 'important'); n++; } }
  return n; }"""
_RESTORE_OVERLAYS = """() => { for (const [e, v, p] of (window.__ainotateHidden || []))
  v ? e.style.setProperty('display', v, p) : e.style.removeProperty('display');
  window.__ainotateHidden = []; }"""
# The text's own box: union of the client rects of the matched text node range (viewport CSS px).
# `auto` skips clickable/replaced elements (their element box is the click area); `text` forces it.
_TEXT_BOX = """(el, a) => {
  if (a.fit === 'auto' && (/^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY|LABEL|IMG|SVG|CANVAS|VIDEO)$/i.test(el.tagName)
      || el.closest('a,button,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=option]'))) return null;
  const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT), nodes = [];
  for (let n; (n = tw.nextNode());) if (n.nodeValue.trim() && n.parentElement && !/^(SCRIPT|STYLE)$/.test(n.parentElement.tagName)) nodes.push(n);
  if (!nodes.length) return null;
  const full = nodes.map(n => n.nodeValue).join('');
  const r = document.createRange();
  let i = a.text ? full.toLowerCase().indexOf(a.text.toLowerCase()) : -1, j = i + (a.text || '').length;
  if (i < 0) { i = 0; j = full.length; }
  let pos = 0, s = null, e = null;
  for (const n of nodes) { const len = n.nodeValue.length;
    if (!s && i < pos + len) s = [n, i - pos];
    if (!e && j <= pos + len) { e = [n, j - pos]; break; }
    pos += len; }
  if (!s) return null;
  if (!e) e = [nodes[nodes.length - 1], nodes[nodes.length - 1].nodeValue.length];
  r.setStart(...s); r.setEnd(...e);
  let l = 1e9, t = 1e9, rr = -1e9, b = -1e9;
  for (const c of r.getClientRects()) { if (c.width < 1 || c.height < 1) continue;
    l = Math.min(l, c.left); t = Math.min(t, c.top); rr = Math.max(rr, c.right); b = Math.max(b, c.bottom); }
  return rr > l ? {x: l, y: t, w: rr - l, h: b - t} : null;
}"""
_SCROLL_INTO_VIEW = "el => el.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'})"
_FONTS_READY = "() => Promise.race([document.fonts ? document.fonts.ready : null, new Promise(r => setTimeout(r, 2000))]).then(() => 1)"
# Ambiguity report: the text and a short DOM path of each match.
_DESCRIBE_ALL = """els => els.map(el => {
  const t = (el.innerText || el.value || el.getAttribute('aria-label') || el.textContent || '').replace(/\\s+/g, ' ').trim();
  const seg = e => e.tagName.toLowerCase() + (e.id ? '#' + e.id
    : (typeof e.className === 'string' && e.className.trim() ? '.' + e.className.trim().split(/\\s+/)[0] : ''));
  const parts = [];
  for (let e = el; e && e.nodeType === 1 && e !== document.body && parts.length < 3; e = e.parentElement) {
    parts.unshift(seg(e)); if (e.id) break; }
  return {text: t, path: parts.join(' > ')};
})"""
# Visible short texts of a document (for "closest visible texts" when a text target is missing).
_VISIBLE_TEXTS = """() => { const out = new Set();
  const tw = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  for (let n; (n = tw.nextNode()) && out.size < 600;) {
    const p = n.parentElement, s = n.nodeValue.replace(/\\s+/g, ' ').trim();
    if (!p || !s || s.length > 80 || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(p.tagName)) continue;
    if (p.checkVisibility && !p.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
    const r = p.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;
    out.add((p.innerText || s).replace(/\\s+/g, ' ').trim() || s); }
  return [...out]; }"""
# Freeze the page before the privacy scan + screenshot: CSS animations and transitions stop,
# media pauses, carets disappear. Reversible (_UNFREEZE) for tabs that are not ours.
_FREEZE = """() => { if (window.__ainotateFrozen) return 0;
  const st = document.createElement('style'); st.id = '__ainotate_freeze';
  st.textContent = '*, *::before, *::after { animation-play-state: paused !important; transition: none !important;'
    + ' caret-color: transparent !important; scroll-behavior: auto !important; }';
  (document.head || document.documentElement).appendChild(st);
  const media = [...document.querySelectorAll('video, audio')].filter(m => !m.paused);
  media.forEach(m => { try { m.pause(); } catch (e) {} });
  const anims = document.getAnimations ? document.getAnimations().filter(a => a.playState === 'running') : [];
  anims.forEach(a => { try { a.pause(); } catch (e) {} });
  window.__ainotateFrozen = {st, media, anims}; return 1; }"""
_UNFREEZE = """() => { const f = window.__ainotateFrozen; if (window.__ainotateMut) { window.__ainotateMut.obs.disconnect(); delete window.__ainotateMut; }
  if (!f) return 0; f.st.remove(); f.anims.forEach(a => { try { a.play(); } catch (e) {} });
  f.media.forEach(m => { try { m.play().catch(() => {}); } catch (e) {} }); delete window.__ainotateFrozen; return 1; }"""
# DOM mutations between the privacy scan and the screenshot. Script/meta/link churn in <head>
# (analytics tags) does not change what is drawn, so it does not count.
_WATCH_START = """() => { if (window.__ainotateMut) window.__ainotateMut.obs.disconnect();
  const inert = n => n.nodeType === 1 && /^(SCRIPT|META|NOSCRIPT|LINK|TEMPLATE)$/.test(n.tagName) && !(n.tagName === 'LINK' && /stylesheet/i.test(n.rel || ''));
  const counts = r => !(r.type === 'childList' && [...r.addedNodes, ...r.removedNodes].every(inert))
    && !(r.type === 'attributes' && inert(r.target));
  const w = {n: 0, counts};
  w.obs = new MutationObserver(rs => { w.n += rs.filter(counts).length; });
  w.obs.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  window.__ainotateMut = w; return 1; }"""
_WATCH_COUNT = """() => { const w = window.__ainotateMut; if (!w) return -1;
  const n = w.n + w.obs.takeRecords().filter(w.counts).length; w.obs.disconnect(); delete window.__ainotateMut; return n; }"""
