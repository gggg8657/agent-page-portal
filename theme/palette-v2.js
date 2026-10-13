/* v2 포털 화면 전용 — Ctrl+K(⌘K) 명령 팔레트 + 머리줄 위치 제목. portal.py theme_head 가 v2 포털 화면에만 붙임(defer).
   도구 찾기·열기, 포털 화면 이동, 테마·설명서. 기능은 원래 화면 것을 그대로 부른다(go()·hit 기록·/api/tools). */
(function () {
  if (window.__v2pal || location.pathname === "/login") return; window.__v2pal = 1;  // 로그인 화면은 팔레트 없음(로그인 전 API 호출 안 함)
  var D = document, $ = function (s, r) { return (r || D).querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };

  // 머리줄 제목: 홈은 고른 메뉴, 계정 화면은 주소
  function title() {
    var t = $("#v2Title"); if (!t) return;
    var on = $("#menu div.on"), P = { "/account": "계정 설정", "/approvals": "승인", "/admin": "관리" };
    t.textContent = on ? on.textContent.trim() : P[location.pathname] || "홈";
  }
  var menu = $("#menu");
  if (menu && window.MutationObserver) new MutationObserver(title).observe(menu, { subtree: true, attributes: true, attributeFilter: ["class"] });
  title();

  var me = null, tools = [], got = 0;
  function load() {
    if (Date.now() - got < 10000) return Promise.resolve();
    return fetch("/api/tools").then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { if (j) { tools = j.tools || []; me = j.me || null; got = Date.now(); } }).catch(function () {});
  }
  function pages() {
    var home = location.pathname === "/";
    var L = [
      ["홈", "포털", function () { home ? pick("home") : (location.href = "/"); }],
      ["전체 에이전트", "포털", function () { home ? pick("all") : (location.href = "/#all"); }],
      ["즐겨찾기", "포털", function () { home ? pick("fav") : (location.href = "/"); }],
      ["최근 사용", "포털", function () { home ? pick("recent") : (location.href = "/"); }],
      ["내 에이전트 만들기", "포털", function () { home && window.addTool ? addTool.showModal() : (location.href = "/"); }],
      ["노하우 글쓰기", "포털", function () { home && window.newPost ? newPost.showModal() : (location.href = "/"); }],
      ["계정 설정 · PIN 바꾸기", "계정", function () { location.href = "/account"; }]
    ];
    if (me && (me.owner || (me.heads || []).length)) L.push(["승인", "계정", function () { location.href = "/approvals"; }]);
    if (me && me.owner) L.push(["관리", "계정", function () { location.href = "/admin"; }]);
    L.push(["사용 설명서", "도움말", function () { window.open("/guide/"); }]);
    if ($("[data-ag-theme-btn]")) L.push(["화면 테마 바꾸기", "설정", function () { setTimeout(function () { $("[data-ag-theme-btn]").click(); }, 30); }]);
    else L.push(["화면 테마 바꾸기", "설정", function () { location.href = "/account"; }]);
    L.push(["로그아웃", "계정", function () { location.href = "/logout"; }]);
    return L;
  }
  function pick(v) { var m = $('#menu div[data-v="' + v + '"]'); m && m.click(); }
  function openTool(t) {
    if (typeof window.go === "function") return window.go(t.dir);  // 홈: 원래 동작(열기·기록, 꺼져 있으면 실행 방법)
    if (t.up) { fetch("/api/open", { method: "POST", body: JSON.stringify({ dir: t.dir }) }).catch(function () {}); window.open(t.url); }
    else location.href = "/#all";
  }

  var dlg, inp, list, items = [], sel = 0;
  function build() {
    dlg = D.createElement("dialog"); dlg.id = "v2pal"; dlg.setAttribute("aria-label", "명령 팔레트");
    dlg.innerHTML = '<div class="pi"><svg viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.3"/><path d="m10.2 10.2 3.3 3.3"/></svg><input type="text" placeholder="도구 이름·할 일 또는 화면 이동 (예: 회의록, 계정)" aria-label="찾기" autocomplete="off"><kbd>Esc</kbd></div>' +
      '<ul role="listbox"></ul><div class="pf"><span><kbd>↑</kbd> <kbd>↓</kbd> 고르기</span><span><kbd>Enter</kbd> 열기</span><span><kbd>Ctrl K</kbd> 열고 닫기</span></div>';
    D.body.appendChild(dlg);
    inp = $("input", dlg); list = $("ul", dlg);
    inp.addEventListener("input", function () { sel = 0; draw(); });
    inp.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (items.length) { sel = (sel + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length; draw(true); } }
      else if (e.key === "Enter") { e.preventDefault(); run(sel); }
    });
    list.addEventListener("click", function (e) { var li = e.target.closest("li.o"); if (li) run(+li.dataset.i); });
    list.addEventListener("mousemove", function (e) { var li = e.target.closest("li.o"); if (li && +li.dataset.i !== sel) { sel = +li.dataset.i; draw(true); } });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
  }
  function draw(keep) {
    var q = inp.value.trim().toLowerCase(), H = [];
    var T = tools.filter(function (t) { return !q || (t.title + " " + t.desc + " " + (t.tags || []).join(" ") + " " + t.group + " " + t.dir).toLowerCase().indexOf(q) >= 0; })
      .sort(function (a, b) { return (b.up - a.up) || ((b.usage || {}).total || 0) - ((a.usage || {}).total || 0); }).slice(0, q ? 30 : 8);
    var P = pages().filter(function (p) { return !q || (p[0] + " " + p[1]).toLowerCase().indexOf(q) >= 0; });
    items = [];
    if (T.length) { H.push('<li class="h">에이전트</li>'); T.forEach(function (t) { items.push(function () { openTool(t); }); H.push('<li class="o" role="option" data-i="' + (items.length - 1) + '"><i class="' + (t.up ? "up" : "") + '"></i><span>' + esc(t.title) + '</span><small>' + esc(t.group) + (t.up ? " · 실행 중" : " · 중지") + "</small></li>"); }); }
    if (P.length) { H.push('<li class="h">이동 · 설정</li>'); P.forEach(function (p) { items.push(p[2]); H.push('<li class="o" role="option" data-i="' + (items.length - 1) + '"><span>' + esc(p[0]) + "</span><small>" + esc(p[1]) + "</small></li>"); }); }
    if (!items.length) H.push('<li class="none">찾는 것이 없습니다</li>');
    if (sel >= items.length) sel = 0;
    list.innerHTML = H.join("");
    var s = list.querySelector('li.o[data-i="' + sel + '"]'); if (s) { s.classList.add("sel"); s.setAttribute("aria-selected", "true"); if (keep) s.scrollIntoView({ block: "nearest" }); }
  }
  function run(i) { var f = items[i]; if (!f) return; dlg.close(); f(); }
  function open() {
    if (!dlg) build();
    if (dlg.open) { dlg.close(); return; }
    inp.value = ""; sel = 0; draw(); dlg.showModal(); inp.focus();
    load().then(function () { if (dlg.open) draw(); });
  }
  D.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K")) { e.preventDefault(); open(); }
  });
  D.addEventListener("click", function (e) { if (e.target.closest && e.target.closest("[data-v2-cmd]")) open(); });
  load();
})();
