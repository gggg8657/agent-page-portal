/* 어둡게 테마 — 자체 어둡게 모드가 없는 도구 화면의 CSS 고정 색을 OKLCH 명도만 바꿔 어둡게 만든다(Dark Reader 와 같은 방식, 아주 작게).
   - 바탕(background·border): 밝은 색 → 어두운 표면(--ag-surface 기준), 이미 어두운 색은 그대로
   - 글자(color·fill): 어두운 색 → 밝은 글자(--ag-ink 기준), 이미 밝은 글자(흰 글씨 버튼 등)는 그대로
   - 변수(--x): 값으로 판단 — 진한 색(강조색)은 명도만 가운데로, 나머지는 바탕/글자
   그림·캔버스·영상·SVG 속성은 건드리지 않는다. 테마를 밝게 바꾸면 원래 값으로 되돌린다. */
(function () {
  if (window.__agDark) return;
  var D = document.documentElement, undo = [], seenSheets = new WeakSet(), inlineDone = new WeakMap(), ACTIVE = false, NATIVE = false;
  var dark = function () { return /dark$/.test(D.getAttribute("data-ag-theme") || ""); };

  // ── 색 변환 (sRGB ↔ OKLab) ──
  var lin = function (c) { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  var gam = function (c) { c = c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; return Math.round(Math.max(0, Math.min(1, c)) * 255); };
  function ok(r, g, b) {
    r = lin(r); g = lin(g); b = lin(b);
    var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b), s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    var L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
    return [L, Math.sqrt(A * A + B * B), Math.atan2(B, A)];
  }
  function rgb(L, C, h) {
    var A = C * Math.cos(h), B = C * Math.sin(h);
    var l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3), m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3), s = Math.pow(L - 0.0894841775 * A - 1.291485548 * B, 3);
    return [gam(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s), gam(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s), gam(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)];
  }
  function parse(s) {
    s = s.trim().toLowerCase();
    if (s === "white") return [255, 255, 255, 1];
    if (s === "black") return [0, 0, 0, 1];
    if (s[0] === "#") {
      var h = s.slice(1); if (h.length < 6) h = h.split("").map(function (c) { return c + c; }).join("");
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), h.length >= 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1];
    }
    var n = s.match(/[\d.]+%?/g); if (!n || n.length < 3) return null;
    var a = n[3] === undefined ? 1 : /%$/.test(n[3]) ? parseFloat(n[3]) / 100 : parseFloat(n[3]);
    return [parseFloat(n[0]), parseFloat(n[1]), parseFloat(n[2]), a];
  }
  var T = {};  // 토큰 명도 (테마마다)
  function tokens() {
    var cs = getComputedStyle(D), g = function (k, d) { var p = parse(cs.getPropertyValue(k) || d) || parse(d); return ok(p[0], p[1], p[2]); };
    T = { bg: g("--ag-bg", "#111419"), sf: g("--ag-surface", "#1a1e25"), ink: g("--ag-ink", "#e6e8ec") };
  }
  // role: bg | page | bd | fg | var
  function mapColor(c, role) {
    var p = parse(c); if (!p) return null;
    var o = ok(p[0], p[1], p[2]), L = o[0], C = o[1], h = o[2], L2, C2 = C;
    if (role === "var") role = (C > 0.09 && L > 0.3 && L < 0.86) ? "acc" : L > 0.6 ? "bg" : L < 0.5 ? "fg" : null;
    if (!role) return null;
    if (role === "acc") { if (L >= 0.45 && L <= 0.6) return null; L2 = Math.min(Math.max(L, 0.45), 0.6); }  // 흰 글씨 버튼 바탕으로도 쓰이므로 가운데 명도
    else if (role === "fgacc") { if (C < 0.06 || L >= 0.75) return null; L2 = 0.76; C2 = Math.min(C, 0.16); }  // 강조색을 글자로 쓴 곳(color:var(--acc)) → 밝게
    else if (role === "fg") { if (L > 0.62) return null; L2 = T.ink[0] * (1 - 0.42 * L); C2 = Math.min(C, 0.17); }
    else {
      if (L < 0.5) return null;  // 이미 어두운 바탕(남색 버튼 등)·그림자는 그대로
      var base = role === "page" ? T.bg : T.sf;
      L2 = role === "bd" ? base[0] + 0.07 + (1 - L) * 0.5 : base[0] + (1 - L) * (role === "page" ? 0.6 : 1.05);
      if (C < 0.02) { C2 = base[1]; h = base[2]; } else C2 = Math.min(C * 0.55, 0.09);  // 회색이면 테마 표면의 옅은 색조를 따름
    }
    var q = rgb(Math.min(L2, 0.97), C2, h);
    return p[3] < 1 ? "rgba(" + q.join(", ") + ", " + +p[3].toFixed(3) + ")" : "#" + q.map(function (v) { return (v < 16 ? "0" : "") + v.toString(16); }).join("");
  }
  var COLOR_RE = /#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|\b(?:white|black)\b/gi;
  function roleOf(prop, sel) {
    if (prop.slice(0, 2) === "--") return "var";
    if (prop === "color" || prop === "fill" || prop === "stroke" || prop === "caret-color" || prop === "-webkit-text-fill-color" || prop === "text-decoration-color") return "fg";
    if (prop === "background-color" || prop === "background-image") return /^\s*(html|body)\s*$/i.test(sel || "") ? "page" : "bg";
    if (/^(border-(top|right|bottom|left)-color|outline-color|column-rule-color)$/.test(prop)) return "bd";
    return null;
  }
  function fixStyle(st, sel) {
    for (var i = 0; i < st.length; i++) {
      var prop = st[i], role = roleOf(prop, sel); if (!role) continue;
      var v = st.getPropertyValue(prop); if (!v) continue;
      if (role === "fg" && /^\s*var\(--[\w-]+\)\s*$/.test(v)) { later.push([st, prop, v]); continue; }  // 변수 글자색은 변수 값이 바뀐 뒤에
      if (v.indexOf("var(") >= 0 && !/#|rgb|white|black/i.test(v)) continue;
      var changed = false, nv = v.replace(COLOR_RE, function (c) { var m = mapColor(c, role); if (m) { changed = true; return m; } return c; });
      if (changed) { var pr = st.getPropertyPriority(prop); undo.push([st, prop, v, pr]); st.setProperty(prop, nv, pr); }
    }
  }
  var later = [];
  function fixLater() {  // color:var(--acc) 처럼 강조색 변수를 글자에 쓴 곳 → 밝은 강조색
    var cs = getComputedStyle(D);
    later.splice(0).forEach(function (x) {
      var val = cs.getPropertyValue(x[2].trim().slice(4, -1).trim()).trim(), m = val && mapColor(val, "fgacc");
      if (m) { var pr = x[0].getPropertyPriority(x[1]); undo.push([x[0], x[1], x[2], pr]); x[0].setProperty(x[1], m, pr); }
    });
  }
  function walk(rules) {
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      if (r.media && /prefers-color-scheme\s*:\s*dark|^\s*print\s*$/i.test(r.media.mediaText)) continue;  // 도구 자체 어둡게 규칙·인쇄용은 그대로
      if (r.style && r.selectorText !== undefined) fixStyle(r.style, r.selectorText);
      else if (r.style && r.type === 1) fixStyle(r.style, "");
      if (r.cssRules) walk(r.cssRules);
    }
  }
  function sheetOk(sh) {  // 테마 자체 CSS·SVG 안 <style>(Mermaid 등 그림 내용)은 건너뜀
    var n = sh.ownerNode;
    return !(sh.href && /\/theme\//.test(sh.href)) && !(n && n.id && /^ag-th-/.test(n.id)) && !(n && n.closest && n.closest("svg"));
  }
  function hasNative(sh) {
    try { return Array.prototype.some.call(sh.cssRules, function (r) { return /\[data-theme=["']?dark/.test(r.selectorText || "") || (r.cssRules && hasNative({ cssRules: r.cssRules })); }); } catch (e) { return false; }
  }
  function doSheet(sh) {
    if (seenSheets.has(sh) || !sheetOk(sh)) return;
    var rules; try { rules = sh.cssRules; } catch (e) { return; }  // 다른 출처 CSS 는 못 읽음 — 건너뜀
    seenSheets.add(sh); walk(rules); fixLater();
  }
  function doInline(el) {
    if (!el.style || !el.style.length || inlineDone.get(el) === el.getAttribute("style")) return;
    if (el.closest && el.closest("svg")) return;
    fixStyle(el.style, ""); inlineDone.set(el, el.getAttribute("style"));
  }
  function run() {
    if (!dark()) return;
    if (!ACTIVE) {
      NATIVE = Array.prototype.some.call(document.styleSheets, function (sh) { return sheetOk(sh) && hasNative(sh); });
      if (NATIVE) D.setAttribute("data-ag-native", "1");
    }
    ACTIVE = true; tokens();
    if (!NATIVE) {
      Array.prototype.forEach.call(document.styleSheets, doSheet);
      if (document.body) document.body.querySelectorAll("[style]").forEach(doInline);
    }
    D.removeAttribute("data-ag-dk");
  }
  function restore() {  // 밝게로 바꾸면 원래 값으로
    for (var i = undo.length - 1; i >= 0; i--) { var u = undo[i]; try { u[0].setProperty(u[1], u[2], u[3]); } catch (e) {} }
    undo = []; seenSheets = new WeakSet(); inlineDone = new WeakMap(); ACTIVE = false; D.removeAttribute("data-ag-native");
  }
  var pending = false;
  new MutationObserver(function (ms) {  // 나중에 붙는 <style>·style="" (도구 스크립트가 그리는 화면)
    if (!ACTIVE || NATIVE) return;
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      if (m.type === "attributes") { doInline(m.target); continue; }
      for (var j = 0; j < m.addedNodes.length; j++) {
        var n = m.addedNodes[j]; if (n.nodeType !== 1) continue;
        if (n.tagName === "STYLE" || n.tagName === "LINK") { if (n.sheet) doSheet(n.sheet); else n.addEventListener("load", function () { this.sheet && doSheet(this.sheet); }); continue; }
        doInline(n); if (n.querySelectorAll) n.querySelectorAll("[style]").forEach(doInline);
        if (!pending) { pending = true; requestAnimationFrame(function () { pending = false; Array.prototype.forEach.call(document.styleSheets, doSheet); }); }
      }
    }
  }).observe(D, { childList: true, subtree: true, attributes: true, attributeFilter: ["style"] });
  new MutationObserver(function () { if (dark()) { if (ACTIVE) { restore(); } run(); } else if (ACTIVE) restore(); }).observe(D, { attributes: true, attributeFilter: ["data-ag-theme"] });
  window.__agDark = { run: run, restore: restore, map: mapColor };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run); else run();
  addEventListener("load", function () { if (ACTIVE && !NATIVE) Array.prototype.forEach.call(document.styleSheets, doSheet); });
})();
