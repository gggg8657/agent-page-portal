/* 테마 고르기 — 포털 화면 전용. [data-ag-theme-picker] 칸에 4가지 카드 + '시스템 따라 자동' 을 그린다.
   위쪽 막대: <button data-ag-theme-btn> 누르면 같은 내용이 떠 있는 창으로. 고르면 window.agTheme.set() (boot.js) → 계정에 저장 */
(function () {
  var OPTS = [
    ["classic-light", "클래식 · 밝게", "지금까지의 화면", { bg: "#f3f5f8", sf: "#fff", ln: "#e5e7eb", ink: "#1f2937", mu: "#9ca3af", ac: "#1f3a5f", r: 3 }],
    ["classic-dark", "클래식 · 어둡게", "같은 배치, 어두운 색", { bg: "#111419", sf: "#1a1e25", ln: "#2b313a", ink: "#e6e8ec", mu: "#5d6672", ac: "#3268d6", r: 3 }],
    ["trendy-light", "트렌디 · 밝게", "둥근 카드·부드러운 그림자", { bg: "#f6f6f9", sf: "#fff", ln: "#e7e7ed", ink: "#15151c", mu: "#b9b9c6", ac: "linear-gradient(135deg,#6c63ff,#3f7bf0)", r: 6, sh: "0 2px 6px rgba(20,20,40,.10)" }],
    ["trendy-dark", "트렌디 · 어둡게", "깊은 바탕·인디고 강조", { bg: "#0c0c10", sf: "#17171d", ln: "#2a2a34", ink: "#ededf2", mu: "#4a4a57", ac: "linear-gradient(135deg,#7c74ff,#3f7bf0)", r: 6, sh: "0 2px 8px rgba(0,0,0,.6)" }]
  ];
  var css = document.createElement("style");
  css.textContent =
    ".agtp{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;font-size:13px}" +
    ".agtp button.o{all:unset;box-sizing:border-box;cursor:pointer;display:flex;flex-direction:column;gap:6px;padding:8px;border-radius:12px;border:1.5px solid var(--line,#e5e7eb);background:var(--card,#fff);color:var(--fg,#1f2937);transition:border-color .15s,box-shadow .15s}" +
    ".agtp button.o:hover{border-color:var(--muted,#9ca3af)}.agtp button.o:focus-visible{outline:2px solid var(--blue,#2563eb);outline-offset:2px}" +
    ".agtp button.o.on{border-color:var(--blue,#2563eb);box-shadow:0 0 0 3px color-mix(in srgb,var(--blue,#2563eb) 22%,transparent)}" +
    ".agtp .pv{position:relative;height:74px;border-radius:8px;overflow:hidden;border:1px solid rgba(127,127,127,.25)}" +
    ".agtp .pv i{position:absolute;display:block}" +
    ".agtp .t{display:flex;align-items:center;gap:6px;font-weight:650;line-height:1.3}.agtp .t em{font-style:normal;font-size:10.5px;font-weight:600;padding:0 6px;border-radius:99px;background:var(--chip,#eef2f7);color:var(--muted,#6b7280)}" +
    ".agtp .d{font-size:11.5px;color:var(--muted,#6b7280);line-height:1.35}" +
    ".agtp .auto{grid-column:1/-1;display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--muted,#6b7280);padding:2px 2px 0;cursor:pointer}.agtp .auto input{margin:0;accent-color:var(--blue,#2563eb)}" +
    ".agtp-pop{position:absolute;right:0;top:calc(100% + 8px);z-index:1000;width:min(420px,calc(100vw - 24px));padding:14px;border-radius:16px;background:var(--card,#fff);border:1px solid var(--line,#e5e7eb);box-shadow:0 18px 50px -12px rgba(0,0,0,.35)}" +
    ".agtp-pop h4{margin:0 0 10px;font-size:13px;color:var(--fg,#1f2937);display:flex;justify-content:space-between;gap:8px}.agtp-pop h4 small{font-weight:400;color:var(--muted,#6b7280)}" +
    ".agtp-wrap{position:relative;display:inline-flex}";
  document.head.appendChild(css);

  function preview(c) {
    var s = function (o) { return Object.keys(o).map(function (k) { return k + ":" + o[k]; }).join(";"); };
    return '<div class="pv" style="background:' + c.bg + '">' +
      '<i style="' + s({ left: 0, right: 0, top: 0, height: "13px", background: c.sf, "border-bottom": "1px solid " + c.ln }) + '"></i>' +
      '<i style="' + s({ left: "7px", top: "4px", width: "28px", height: "5px", "border-radius": "3px", background: c.ink }) + '"></i>' +
      '<i style="' + s({ left: "7px", top: "20px", width: "22%", bottom: "7px", "border-radius": c.r + "px", background: c.sf, border: "1px solid " + c.ln, "box-shadow": c.sh || "none" }) + '"></i>' +
      '<i style="' + s({ left: "calc(22% + 13px)", right: "7px", top: "20px", height: "22px", "border-radius": c.r + "px", background: c.sf, border: "1px solid " + c.ln, "box-shadow": c.sh || "none" }) + '"></i>' +
      '<i style="' + s({ left: "calc(22% + 19px)", top: "27px", width: "38%", height: "4px", "border-radius": "2px", background: c.mu }) + '"></i>' +
      '<i style="' + s({ left: "calc(22% + 13px)", top: "48px", width: "26%", height: "14px", "border-radius": c.r + "px", background: c.sf, border: "1px solid " + c.ln, "box-shadow": c.sh || "none" }) + '"></i>' +
      '<i style="' + s({ right: "7px", top: "48px", width: "24%", height: "14px", "border-radius": c.r + "px", background: c.ac }) + '"></i></div>';
  }
  function render(el) {
    var T = window.agTheme; if (!T) { el.textContent = "테마를 불러오지 못했습니다"; return; }
    var pref = T.pref, auto = /auto$/.test(pref), cur = T.theme;
    el.innerHTML = '<div class="agtp" role="radiogroup" aria-label="화면 테마">' + OPTS.map(function (o) {
      var on = auto ? o[0] === cur : o[0] === pref;
      return '<button type="button" class="o' + (on ? " on" : "") + '" role="radio" aria-checked="' + on + '" data-v="' + o[0] + '">' + preview(o[3]) +
        '<span class="t">' + o[1] + (on && auto ? "<em>자동</em>" : "") + '</span><span class="d">' + o[2] + "</span></button>";
    }).join("") + '<label class="auto"><input type="checkbox"' + (auto ? " checked" : "") + '> 컴퓨터 설정 따라 밝게·어둡게 자동으로</label></div>';
    el.querySelectorAll("button.o").forEach(function (b) {
      b.onclick = function () {
        var v = b.dataset.v, a = el.querySelector(".auto input").checked;
        T.set(a ? v.split("-")[0] + "-auto" : v);
      };
    });
    el.querySelector(".auto input").onchange = function (e) { T.set(e.target.checked ? T.pref.split("-")[0] + "-auto" : T.theme); };
  }
  var mounted = [];
  function mount(el) { if (!el || el.__agtp) return; el.__agtp = 1; mounted.push(el); render(el); }
  addEventListener("ag-theme", function () { mounted.forEach(render); });
  function button(btn) {  // 위쪽 막대 버튼 → 떠 있는 창
    if (btn.__agtp) return; btn.__agtp = 1;
    var wrap = document.createElement("span"); wrap.className = "agtp-wrap"; btn.parentNode.insertBefore(wrap, btn); wrap.appendChild(btn);
    var pop = document.createElement("div"); pop.className = "agtp-pop"; pop.hidden = true;
    pop.innerHTML = '<h4>화면 테마 <small>모든 도구 화면에 같이 적용</small></h4><div></div>'; wrap.appendChild(pop);
    btn.setAttribute("aria-expanded", "false"); btn.setAttribute("aria-haspopup", "dialog");
    var close = function () { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", function (e) { e.stopPropagation(); pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); if (!pop.hidden) { mount(pop.lastChild); var on = pop.querySelector(".on"); on && on.focus(); } });
    document.addEventListener("click", function (e) { if (!pop.hidden && e.target.isConnected && !wrap.contains(e.target)) close(); });  // 고르면 다시 그려져 눌린 버튼이 빠짐(isConnected) → 창 유지
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !pop.hidden) { close(); btn.focus(); } });
  }
  function scan() { document.querySelectorAll("[data-ag-theme-picker]").forEach(mount); document.querySelectorAll("[data-ag-theme-btn]").forEach(button); }
  window.agThemePicker = { mount: mount, scan: scan };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan); else scan();
})();
