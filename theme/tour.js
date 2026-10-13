/* 첫 로그인 둘러보기 — 포털 홈(classic·trendy 의 portal.html, v2 의 v2/home.html) 공통. portal.py 가 홈에만 붙임(help.html 보다 앞).
   흐름: ② 환영 창(이름·부서·테마) → ③~⑥ 둘러보기 4단계(실제 화면 요소를 비춤) → ⑦ 끝. 와이어프레임: Welcome·Tour1~4·Finish.
   - 자동으로 뜨는 때: /api/me 의 onboarded=false(새 가입자). 건너뛰기·마치기 둘 다 POST /api/account/onboarded {"done":true}
   - 다시 보기: ? 사용법 창의 '둘러보기 다시 보기'(window.agTour.start()) 또는 /#tour — 기록은 그대로
   - 테마 계열(v2 ↔ 다른 계열)을 바꾸면 화면이 새로고침되므로 진행 상태는 sessionStorage 'agTour' 로 이어 감
   - 대상 요소는 아래 TARGETS 표(레이아웃별 선택자). 없거나 안 보이면 그 단계는 가운데 말풍선(휴대폰 폭은 아래쪽 시트) */
(function () {
  if (window.top !== window || window.agTour) return;
  var D = document, SS = "agTour", N = 4;
  var $ = function (s) { return D.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };
  var ss = function (v) { try { if (v === undefined) return JSON.parse(sessionStorage.getItem(SS) || "null"); if (v === null) sessionStorage.removeItem(SS); else sessionStorage.setItem(SS, JSON.stringify(v)); } catch (e) { return null; } };
  var layout = function () { return $(".v2") ? "v2" : "top"; };  // top = classic·trendy(위쪽 막대 + 왼쪽 상자), v2 = 왼쪽 메뉴

  // 단계별 비출 요소 — 레이아웃마다 선택자 표. 여러 개면 보이는 것들을 합친 사각형
  var TARGETS = {
    search:  { top: [".search"], v2: [".search"] },
    tools:   { top: ["#chips", "#home:not([hidden]) #cols", "#all:not([hidden]) #grid"], v2: ["#chips", "#home:not([hidden]) #cols", "#all:not([hidden]) #grid"] },
    help:    { top: ["#agHelpBtn"], v2: ["#agHelpBtn"] },
    account: { top: ["#user"], v2: ["#user"] }
  };
  var pet = function () { var p = $("#agPet"); return p && visible(p) ? p : null; };  // 파동이 펫(padong-local 이 꺼져 있으면 없음)
  var STEPS = [
    { k: "search", tag: "찾기", title: function () { return "하고 싶은 일을 말로 찾으세요"; },
      body: function () { return "<p>\"회의록\", \"HWP 요약\", \"그래프\"처럼 쓰고 Enter 를 누르면 맞는 도구만 남습니다." +
        (window.__v2pal ? " 어느 포털 화면에서든 <b>Ctrl + K</b> 를 누르면 바로 열립니다." : "") + "</p>"; } },
    { k: "tools", tag: "도구 고르기", title: function () { return "분류로 좁히고, 카드를 눌러 여세요"; },
      body: function () { return "<p>위쪽 칩으로 문서·그림·데이터처럼 분류를 고르면 그 도구만 남습니다. 도구를 누르면 새 탭에서 열립니다. " +
        "꺼져 있는 도구는 <b>전체 에이전트</b>에서 <b>시작</b>을 누르면 켜지고, ☆ 을 누르면 즐겨찾기에 들어갑니다.</p>"; } },
    { k: "help", tag: "막히면", title: function (r) { return "어느 화면이든 " + (r && r.top > innerHeight / 2 ? "오른쪽 아래" : "오른쪽 위") + " ? 가 사용법"; },
      body: function () { return "<p>도구마다 쓰는 순서와 주의할 점이 들어 있습니다. 영상·구조 예측처럼 오래 걸리는 작업은 창을 열어 둔 채 다른 일을 하세요." +
        (pet() ? " 끝나면 화면 아래의 <b>파동이</b>가 알려 줍니다." : "") + "</p>"; } },
    { k: "account", tag: "내 계정", title: function () { return "부서·테마·PIN 은 계정 설정에서"; },
      body: function () { return "<ul><li>부서를 바꾸면 새 부서장에게 승인 요청이 갑니다</li><li>부서장 권한이 필요하면 관리자에게 요청하세요</li>" +
        "<li>내 작업 기록은 나만 봅니다. 주간보고 취합처럼 같이 쓰는 것은 도구 화면에 표시돼 있습니다</li></ul>"; } }
  ];
  var FAM = [["classic", "클래식", "예전 그대로", "#2B4C7E"], ["trendy", "트렌디", "둥글고 부드러움", "#5B5BD6"], ["v2", "v2 작업대", "촘촘·청록 강조", "#0E7268"]];

  var css = D.createElement("style");
  css.textContent = [
    "#agTour{--t-sf:var(--ag-surface,#fff);--t-ink:var(--ag-ink,#1f2937);--t-ink2:var(--ag-ink-2,#4b5563);--t-line:var(--ag-line,#e5e7eb);--t-line2:var(--ag-line-2,#d1d5db);",
    "--t-acc:var(--ag-accent,#1f3a5f);--t-btn:var(--ag-accent,#1f3a5f);--t-acc-ink:var(--ag-accent-ink,#1f3a5f);--t-soft:var(--ag-accent-soft,#eef2f7);--t-ring:var(--ag-accent,#2563eb);",
    "--t-dim:var(--ag-backdrop,rgba(15,23,42,.5));--t-r:var(--ag-r-lg,10px);--t-rc:var(--ag-r-sm,6px);--t-sh:var(--ag-shadow-3,0 16px 40px rgba(15,23,42,.28));--t-bad:var(--ag-bad,#dc2626);",
    "font:14px/1.6 var(--ag-sans,-apple-system,\"Pretendard\",\"Apple SD Gothic Neo\",system-ui,sans-serif);color:var(--t-ink);letter-spacing:normal;text-align:left}",
    "html[data-ag-theme^=classic] #agTour{--t-btn:var(--ag-brand,#1f3a5f);--t-acc-ink:var(--ag-brand-ink,#1f3a5f)}",
    "html[data-ag-theme^=trendy] #agTour{--t-btn:var(--ag-grad)}",
    "html[data-ag-theme$=dark] #agTour{--t-dim:rgba(0,0,0,.62)}",
    "#agTour *{box-sizing:border-box}",
    "#agTour .tv{position:fixed;inset:0;z-index:2147483000;background:transparent}",
    "#agTour .tv.dim{background:var(--t-dim)}",
    "#agTour .th{position:fixed;z-index:2147483001;border-radius:10px;pointer-events:none;box-shadow:0 0 0 3px var(--t-ring),0 0 0 200vmax var(--t-dim);transition:all .18s ease}",
    "#agTour .th.ring{box-shadow:0 0 0 3px var(--t-ring)}",
    "#agTour .tp{position:fixed;z-index:2147483001;pointer-events:none;border-radius:50%;border:3px dashed var(--t-ring)}",
    "#agTour .tb{position:fixed;z-index:2147483002;width:400px;max-width:calc(100vw - 24px);background:var(--t-sf);color:var(--t-ink);border:1px solid var(--t-line);border-radius:var(--t-r);box-shadow:var(--t-sh);padding:20px;display:flex;flex-direction:column;gap:12px;max-height:calc(100vh - 24px);overflow:auto}",
    "#agTour .tb.wel{width:520px;padding:28px;gap:18px}",
    "#agTour .tb:focus{outline:none}",
    "#agTour .ar{position:absolute;width:16px;height:16px;background:var(--t-sf);border:1px solid var(--t-line);transform:rotate(45deg)}",
    "#agTour .ar.t{top:-9px;border-right:none;border-bottom:none}#agTour .ar.b{bottom:-9px;border-left:none;border-top:none}",
    "#agTour .ar.l{left:-9px;border-right:none;border-top:none}#agTour .ar.r{right:-9px;border-left:none;border-bottom:none}",
    "#agTour .kk{font-size:12px;font-weight:700;color:var(--t-acc-ink);margin:0}",
    "#agTour h2{margin:0;font-size:18px;line-height:1.4;letter-spacing:-.01em;color:var(--t-ink);font-weight:700}",
    "#agTour .tb.wel h2{font-size:22px}",
    "#agTour p,#agTour ul{margin:0;font-size:14px;line-height:1.7;color:var(--t-ink2)}#agTour ul{padding-left:18px}#agTour li{margin:2px 0}",
    "#agTour b{color:var(--t-ink)}",
    "#agTour .ft{display:flex;align-items:center;gap:6px;flex-wrap:wrap}",
    "#agTour .dots{display:flex;gap:4px;flex:1}#agTour .dots i{width:6px;height:6px;border-radius:3px;background:var(--t-line2)}#agTour .dots i.on{width:18px;background:var(--t-acc-ink)}",
    "#agTour button.go,#agTour button.lk{min-height:44px;min-width:44px;font:inherit;font-size:14px;cursor:pointer;border-radius:var(--t-rc)}",
    "#agTour button.go{border:none;background:var(--t-btn);color:#fff;font-weight:700;padding:0 18px}",
    "#agTour button.go:hover{filter:brightness(1.08)}",
    "#agTour button.lk{border:none;background:transparent;color:var(--t-ink2);padding:0 12px}",
    "#agTour button.lk:hover{background:var(--t-soft);color:var(--t-ink)}",
    "#agTour :is(button,select,input):focus-visible{outline:2px solid var(--t-ring);outline-offset:2px}",
    "#agTour label.dp{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600;color:var(--t-ink)}",
    "#agTour label.dp span{font-weight:400;font-size:12px;color:var(--t-ink2)}",
    "#agTour select{min-height:44px;width:100%;padding:0 10px;border:1px solid var(--t-line2);border-radius:var(--t-rc);background:var(--t-sf);color:var(--t-ink);font:inherit;font-size:14px}",
    "#agTour fieldset{margin:0;padding:0;border:0;display:flex;flex-direction:column;gap:8px;min-width:0}",
    "#agTour legend{font-size:13px;font-weight:600;padding:0;margin-bottom:8px;color:var(--t-ink)}",
    "#agTour .fam{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}",
    "#agTour .fam button{min-height:88px;padding:10px;display:flex;flex-direction:column;align-items:flex-start;gap:4px;border-radius:var(--t-rc);background:var(--t-sf);color:var(--t-ink);font:inherit;text-align:left;cursor:pointer;border:1px solid var(--t-line2)}",
    "#agTour .fam button[aria-pressed=true]{border:2px solid var(--t-acc-ink);padding:9px;background:var(--t-soft)}",
    "#agTour .fam i{display:block;width:100%;height:24px;border-radius:4px}",
    "#agTour .fam b{font-size:13px}#agTour .fam small{font-size:11px;color:var(--t-ink2);line-height:1.35}",
    "#agTour label.au{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--t-ink2);min-height:32px;cursor:pointer}",
    "#agTour label.au input{width:18px;height:18px;margin:0;accent-color:var(--t-acc-ink)}",
    "#agTour .er{color:var(--t-bad);font-size:13px}#agTour .er:empty{display:none}",
    "#agTour .hd{display:flex;align-items:center;gap:8px}#agTour .hd svg{width:20px;height:20px;fill:none;stroke:var(--t-acc-ink);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round;flex:none}",
    "#agTour .tb.sheet{left:0!important;right:0;width:auto;max-width:none;border-radius:var(--t-r) var(--t-r) 0 0;padding:18px 16px calc(16px + env(safe-area-inset-bottom));max-height:70vh}",
    "#agTour .tb.sheet.up{border-radius:0 0 var(--t-r) var(--t-r)}",
    "@media (max-width:600px){#agTour .tb.wel{padding:20px 16px;gap:14px}#agTour .tb.wel h2{font-size:19px}#agTour .fam button{min-height:76px}}",
    "@media (prefers-reduced-motion:reduce){#agTour .th{transition:none}}"
  ].join("\n");

  var me = null, root = null, st = null, depts = [], raf = 0, tick = 0, inerted = [], lastFocus = null;
  var mobile = function () { return innerWidth <= 600; };
  function visible(el) {
    if (!el || !el.isConnected) return false;
    var r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && +cs.opacity !== 0;
  }
  function targets(k) {
    var sels = (TARGETS[k] || {})[layout()] || [], out = [];
    sels.forEach(function (s) { var e = $(s); if (visible(e)) out.push(e); });
    return out;
  }
  function union(els) {
    var u = null;
    els.forEach(function (e) { var r = e.getBoundingClientRect(); u = u ? { left: Math.min(u.left, r.left), top: Math.min(u.top, r.top), right: Math.max(u.right, r.right), bottom: Math.max(u.bottom, r.bottom) } : { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; });
    if (u) { u.width = u.right - u.left; u.height = u.bottom - u.top; }
    return u;
  }
  function fixedish(el) { for (; el && el !== D.body; el = el.parentElement) { var p = getComputedStyle(el).position; if (p === "fixed" || p === "sticky") return true; } return false; }

  function setInert(on) {
    inerted.forEach(function (e) { e.inert = false; }); inerted = [];
    if (!on) return;
    Array.prototype.forEach.call(D.body.children, function (e) { if (e !== root && !e.inert && e.tagName !== "SCRIPT" && e.tagName !== "STYLE") { e.inert = true; inerted.push(e); } });
  }

  function save() { ss(st && st.i < N ? { i: st.i, dept: st.dept || "" } : null); }  // 끝 카드는 이어 가지 않음
  function done() {  // 건너뛰기·마치기 둘 다 기록(로컬 접속=계정 없음은 안 보냄). ? 창 자동 열기도 이번 계정은 본 것으로
    if (me && me.account) fetch("/api/account/onboarded", { method: "POST", body: JSON.stringify({ done: true }) }).catch(function () {});
    try { if (me) localStorage.setItem("agGuideSeen:" + me.agent_user + ":portal", new Date().toISOString()); } catch (e) {}
    if (me) me.onboarded = true;
  }
  function close() {
    st = null; save(); setInert(false);
    if (root) { root.remove(); root = null; }
    removeEventListener("resize", sched); removeEventListener("scroll", sched, true); D.removeEventListener("keydown", key, true);
    clearInterval(tick); tick = 0;
    var pz = $("#agPet"); if (pz) pz.style.zIndex = "";
    if (lastFocus && lastFocus.isConnected) try { lastFocus.focus(); } catch (e) {}
  }
  function go(i) { st.i = i; save(); render(); }
  function skip() { done(); go(N); }

  function key(e) {
    if (!root || !st) return;
    var bub = root.querySelector(".tb");
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if (st.i >= N) close(); else skip(); return; }
    if (e.key === "Tab" && bub) {  // 초점 가두기
      var f = Array.prototype.filter.call(bub.querySelectorAll("button,select,input,a[href]"), function (x) { return !x.disabled && x.offsetParent !== null; });
      if (!f.length) return;
      if (st.i >= N && !bub.contains(D.activeElement)) return;  // 끝 카드는 모달이 아님
      var a = f[0], z = f[f.length - 1];
      if (e.shiftKey && (D.activeElement === a || !bub.contains(D.activeElement))) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && (D.activeElement === z || !bub.contains(D.activeElement))) { e.preventDefault(); a.focus(); }
      return;
    }
    if (st.i >= 0 && st.i < N && (e.key === "ArrowRight" || e.key === "ArrowLeft") && !/^(SELECT|INPUT|TEXTAREA)$/.test((D.activeElement || {}).tagName || "")) {
      e.preventDefault();
      if (e.key === "ArrowRight") { if (st.i === N - 1) { done(); go(N); } else go(st.i + 1); }
      else if (st.i > 0) go(st.i - 1); else go(-1);
    }
  }
  function sched() { if (!raf) raf = requestAnimationFrame(function () { raf = 0; place(); }); }

  function dots(i) { var s = ""; for (var k = 0; k < N; k++) s += '<i class="' + (k === i ? "on" : "") + '"></i>'; return '<div class="dots" aria-hidden="true">' + s + "</div>"; }
  function themeFam() { var T = window.agTheme; return T ? T.pref.split("-")[0] : "classic"; }
  function themeAuto() { var T = window.agTheme; return T ? /-auto$/.test(T.pref) : false; }

  function render() {
    if (!root) {
      lastFocus = D.activeElement;
      root = D.createElement("div"); root.id = "agTour"; D.body.appendChild(root);
      addEventListener("resize", sched); addEventListener("scroll", sched, true); D.addEventListener("keydown", key, true);
      tick = setInterval(sched, 300);  // 목록 새로고침(10초마다)·파동이 걸음으로 자리가 바뀌어도 따라감
    }
    var i = st.i, h = "";
    if (i < 0) {  // ② 환영
      var askDept = me.account && !me.dept && !me.dept_pending && depts.length;
      h = '<div class="tv dim"></div><div class="tb wel" role="dialog" aria-modal="true" aria-labelledby="agTourT" tabindex="-1">' +
        '<div style="display:flex;flex-direction:column;gap:6px"><p class="kk">처음 오셨네요</p><h2 id="agTourT">' + esc(me.name) + " 님, 환영합니다</h2>" +
        "<p>시작하기 전에 " + (askDept ? "두 가지만" : "화면 테마만") + " 정해 주세요. 나중에 계정 설정에서 바꿀 수 있습니다.</p></div>" +
        (askDept ? '<label class="dp">소속 부서<select id="agTourDept"><option value="">고르지 않음 (나중에)</option>' +
          depts.map(function (d) { return '<option value="' + esc(d) + '"' + (st.dept === d ? " selected" : "") + ">" + esc(d) + "</option>"; }).join("") +
          "</select><span>부서장이 승인하면 반영됩니다. 목록에 없으면 관리자에게 요청하세요.</span></label>" : "") +
        '<fieldset><legend>화면 테마</legend><div class="fam">' + FAM.map(function (f) {
          return '<button type="button" data-f="' + f[0] + '" aria-pressed="' + (themeFam() === f[0]) + '"><i style="background:' + f[3] + '" aria-hidden="true"></i><b>' + f[1] + "</b><small>" + f[2] + "</small></button>";
        }).join("") + '</div><label class="au"><input type="checkbox" id="agTourAuto"' + (themeAuto() ? " checked" : "") + ">밝게·어둡게는 컴퓨터 설정 따라 자동</label></fieldset>" +
        '<p class="er" id="agTourErr" role="alert"></p>' +
        '<div class="ft" style="justify-content:flex-end"><button type="button" class="lk" data-a="skip">둘러보기 건너뛰기</button><button type="button" class="go" data-a="start">1분 둘러보기 시작</button></div></div>';
    } else if (i < N) {  // ③~⑥
      var s = STEPS[i];
      h = '<div class="tv"></div><div class="th"></div><div class="tp" hidden></div><div class="tb" role="dialog" aria-modal="true" aria-labelledby="agTourT" aria-describedby="agTourB" tabindex="-1">' +
        '<span class="ar" hidden></span><p class="kk">' + (i + 1) + " / " + N + " · " + s.tag + '</p><h2 id="agTourT"></h2><div id="agTourB">' + s.body() + "</div>" +
        '<div class="ft">' + dots(i) + '<button type="button" class="lk" data-a="skip">건너뛰기</button>' + (i > 0 ? '<button type="button" class="lk" data-a="prev">이전</button>' : "") +
        '<button type="button" class="go" data-a="next">' + (i === N - 1 ? "마치기" : "다음") + "</button></div></div>";
    } else {  // ⑦ 끝 — 모달 아님, ? 버튼만 테두리
      h = '<div class="th ring"></div><div class="tb" role="status" aria-labelledby="agTourT" tabindex="-1"><div class="hd"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg><h2 id="agTourT">준비 끝</h2></div>' +
        "<p>둘러보기는 언제든 <b>? → 둘러보기 다시 보기</b> 에서 볼 수 있습니다. 자세한 쓰는 법은 위쪽 <b>사용 설명서</b>에 있습니다.</p>" +
        '<div class="ft" style="justify-content:flex-end"><button type="button" class="lk" data-a="again">처음부터 다시</button><button type="button" class="go" data-a="close">시작하기</button></div></div>';
    }
    root.innerHTML = h;
    setInert(i < N);
    root.querySelectorAll("[data-a]").forEach(function (b) { b.onclick = function () { act(b.dataset.a); }; });
    root.querySelectorAll(".fam button").forEach(function (b) { b.onclick = function () { pickTheme(b.dataset.f, null); }; });
    var au = $("#agTourAuto"); if (au) au.onchange = function () { pickTheme(null, au.checked); };
    var dp = $("#agTourDept"); if (dp) dp.onchange = function () { st.dept = dp.value; save(); };
    if (i >= 0 && i < N) {  // 대상이 화면 밖이면 한 번 스크롤(고정 위치 요소는 그대로)
      var els = targets(STEPS[i].k), u = union(els);
      if (u && !els.some(fixedish) && (u.top < 8 || u.bottom > innerHeight - 8)) els[0].scrollIntoView({ block: u.height > innerHeight * 0.6 ? "start" : "center" });
    }
    place();
    var first = root.querySelector(i < 0 ? ".tb" : "button.go");
    if (first) first.focus({ preventScroll: true });
  }

  function pickTheme(fam, auto) {
    var T = window.agTheme; if (!T) return;
    fam = fam || themeFam(); if (auto == null) auto = themeAuto();
    var p = fam + "-" + (auto ? "auto" : T.theme.split("-")[1]);
    if (p === T.pref) return;
    save();  // 계열이 바뀌면 새로고침 → 이어서 환영 창부터
    T.set(p).then(function () { if (root && st && st.i < 0) render(); }).catch(function () {});
    setTimeout(function () { if (root && st && st.i < 0) render(); }, 60);
  }

  function act(a) {
    if (a === "skip") return st.i < 0 ? sendDept().then(function (ok) { if (ok) skip(); }) : skip();
    if (a === "start") return sendDept().then(function (ok) { if (ok) ready(function () { go(0); }); });
    if (a === "next") { if (st.i === N - 1) done(); return go(st.i + 1); }
    if (a === "prev") return go(st.i - 1);
    if (a === "again") return go(-1);
    if (a === "close") return close();
  }
  function sendDept() {  // 환영 창에서 고른 부서 → 기존 부서 요청 API(/api/account/dept, 부서장 승인 대기)
    var d = st.dept; if (!d || !me.account || me.dept || me.dept_pending) return Promise.resolve(true);
    return fetch("/api/account/dept", { method: "POST", body: JSON.stringify({ dept: d }) }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { var e = $("#agTourErr"); if (e) e.textContent = j.error; return false; }
      me.dept_pending = d; st.dept = ""; return true;
    }).catch(function () { var e = $("#agTourErr"); if (e) e.textContent = "서버에 연결하지 못했습니다"; return false; });
  }

  function place() {
    if (!root || !st) return;
    var bub = root.querySelector(".tb"), hole = root.querySelector(".th"), ring = root.querySelector(".tp"), ar = root.querySelector(".ar");
    if (!bub) return;
    var W = innerWidth, H = innerHeight, i = st.i, k = i >= N ? "help" : i >= 0 ? STEPS[i].k : null;
    bub.classList.remove("sheet", "up"); bub.style.left = bub.style.top = bub.style.bottom = ""; if (ar) ar.hidden = true;
    var els = k ? targets(k) : [], u = union(els);
    if (hole) {
      if (u) { var pd = 6; hole.hidden = false; Object.assign(hole.style, { left: u.left - pd + "px", top: u.top - pd + "px", width: u.width + pd * 2 + "px", height: u.height + pd * 2 + "px" }); }
      else hole.hidden = true;
      var tv = root.querySelector(".tv"); if (tv) tv.classList.toggle("dim", !u);
    }
    if (i >= 0 && i < N) {
      var title = $("#agTourT"); if (title) title.textContent = STEPS[i].title(u);
      var pz = $("#agPet");  // 3단계: 파동이를 어둡게 덮지 않고 위로(점선 원과 같이 보이게), 다른 단계에서는 원래대로
      if (pz) pz.style.zIndex = k === "help" ? "2147483001" : "";
      if (ring) { var pp = k === "help" && pet(); ring.hidden = !pp; if (pp) { var r = pp.getBoundingClientRect(), m = 8; Object.assign(ring.style, { left: r.left - m + "px", top: r.top - m + "px", width: r.width + m * 2 + "px", height: r.height + m * 2 + "px" }); } }
    }
    var bw = bub.offsetWidth, bh = bub.offsetHeight, g = 14;
    if (i < 0) { bub.style.left = Math.max(12, (W - bw) / 2) + "px"; bub.style.top = Math.max(12, Math.min(110, (H - bh) / 2)) + "px"; return; }
    if (mobile()) {  // 휴대폰 폭: 아래쪽 시트(대상이 아래쪽이면 위쪽 시트)
      bub.classList.add("sheet");
      if (u && (u.top + u.bottom) / 2 > H * 0.55) { bub.classList.add("up"); bub.style.top = "0px"; bub.style.bottom = "auto"; }
      else { bub.style.bottom = "0px"; bub.style.top = "auto"; }
      return;
    }
    if (!u) { bub.style.left = (W - bw) / 2 + "px"; bub.style.top = Math.max(12, (H - bh) / 2) + "px"; return; }
    var L, T, side;
    if (H - u.bottom - 12 >= bh + g) { side = "t"; T = u.bottom + g; }
    else if (u.top - 12 >= bh + g) { side = "b"; T = u.top - g - bh; }
    else if (W - u.right - 12 >= bw + g) { side = "l"; L = u.right + g; }
    else if (u.left - 12 >= bw + g) { side = "r"; L = u.left - g - bw; }
    if (side === "t" || side === "b") {
      L = Math.max(12, Math.min(W - bw - 12, u.width > bw ? u.left : u.left + u.width / 2 - bw / 2));
      if (u.right > W - 60 && u.width < 120) L = Math.max(12, u.right - bw);  // 오른쪽 끝 작은 버튼(?) — 말풍선 오른쪽을 맞춤
      var ax = u.width > bw ? 32 : u.left + u.width / 2 - L - 8;
      if (ar) { ar.className = "ar " + side; ar.hidden = false; ar.style.left = Math.max(14, Math.min(bw - 30, ax)) + "px"; ar.style.top = ar.style.bottom = ""; }
    } else if (side) {
      T = Math.max(12, Math.min(H - bh - 12, u.top + u.height / 2 - bh / 2));
      if (ar) ar.className = "ar " + side, ar.hidden = false, ar.style.left = "", ar.style.top = Math.max(14, Math.min(bh - 30, u.top + u.height / 2 - T - 8)) + "px";
    } else { L = (W - bw) / 2; T = Math.max(12, H - bh - 16); }
    bub.style.left = L + "px"; bub.style.top = T + "px";
  }

  function ready(cb) {  // 홈 목록·위쪽 막대(#user)가 그려질 때까지(최대 5초)
    var t0 = Date.now();
    (function wait() { var u = $("#user"); if ((u && u.textContent.trim()) || Date.now() - t0 > 5000) cb(); else setTimeout(wait, 120); })();
  }

  function start(i) {
    if (!me) return;
    var help = $("#agHelp"); if (help && help.open) help.close();
    st = { i: i == null ? -1 : i, dept: st && st.dept || "" }; save();
    (me.account ? fetch("/api/account").then(function (r) { return r.json(); }).then(function (j) { depts = j.depts || []; if (j.me) { me.dept = j.me.dept; me.dept_pending = j.me.dept_pending; } }).catch(function () {}) : Promise.resolve())
      .then(function () { if (st.i >= 0) ready(render); else render(); });
  }

  var resolveCheck;
  window.agTourCheck = new Promise(function (r) { resolveCheck = r; });  // help.html: 둘러보기가 뜨면 ? 사용법 창은 자동으로 안 띄움
  D.head.appendChild(css);
  window.agTour = { start: function () { if (!me) return boot(true); start(-1); } };

  function boot(force) {
    return fetch("/api/me").then(function (r) { return r.ok ? r.json() : null; }).then(function (m) {
      me = m; if (!m) return resolveCheck(false);
      var saved = ss(), hash = location.hash === "#tour";
      if (hash) history.replaceState(null, "", location.pathname + location.search);
      var want = force || hash || saved || m.onboarded === false;
      resolveCheck(!!want);
      if (!want) return;
      if (saved) { st = { i: saved.i, dept: saved.dept || "" }; }
      var run = function () { start(saved ? saved.i : -1); };
      if (D.readyState === "loading") D.addEventListener("DOMContentLoaded", run); else run();
    }).catch(function () { resolveCheck(false); });
  }
  boot(false);
})();
