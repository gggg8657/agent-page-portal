(function(){  // 화면 테마 — 포털이 모든 화면 <head> 맨 앞에 끼워 넣음(첫 그림 전에 html[data-ag-theme] 결정). 고르기: 포털 위쪽 '테마' · 계정 설정
  var D = document.documentElement, KIND = "%KIND%", DIR = "%DIR%", V = "%V%", TOOLCSS = %TOOLCSS%, pref = "%PREF%";
  var mq = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
  function resolve(p) { var a = p.split("-"); return a[0] + "-" + (a[1] === "auto" ? (mq && mq.matches ? "dark" : "light") : a[1]); }
  function files(p) {  // portal.py theme_files() 와 같은 목록
    if (p === "classic-light") return [];
    var f = ["tokens.css"], light = /-light$/.test(p);
    if (KIND === "tool") { if (/^trendy/.test(p)) f.push("base-trendy.css"); if (!light) f.push("base-dark.css", "dark.js"); f.push("chrome.css"); if (TOOLCSS) f.push("tools/" + DIR + ".css"); }
    else if (KIND === "guide") f.push("guide.css");
    else f.push("portal.css", "chrome.css");
    return f;
  }
  function ensure(p) {
    files(p).forEach(function (f) {
      var id = "ag-th-" + f.replace(/\W/g, "-");
      if (document.getElementById(id)) return;
      var e = /\.js$/.test(f) ? document.createElement("script") : document.createElement("link");
      if (e.tagName === "LINK") { e.rel = "stylesheet"; e.href = "/theme/" + f + "?v=" + V; } else e.src = "/theme/" + f + "?v=" + V;
      e.id = id; (document.head || D).appendChild(e);
    });
  }
  function apply(p, live) {
    pref = p; var t = resolve(p);
    D.setAttribute("data-ag-pref", p); D.setAttribute("data-ag-theme", t);
    if (p !== "classic-light") D.setAttribute("data-theme", /dark$/.test(t) ? "dark" : "light");  // 자체 어둡게 모드가 있는 도구(:root[data-theme=dark])는 그걸 씀
    else if (live) D.removeAttribute("data-theme");
    if (live) ensure(p);
    window.dispatchEvent(new CustomEvent("ag-theme", { detail: { pref: p, theme: t } }));
  }
  if (KIND === "tool" && /-(dark|auto)$/.test(pref) && /dark$/.test(resolve(pref))) {  // 도구 화면: 색을 바꿀 때까지(dark.js) 잠깐 숨김 — 실패해도 2.5초 뒤엔 보임
    D.setAttribute("data-ag-dk", "wait"); setTimeout(function () { D.removeAttribute("data-ag-dk"); }, 2500);
  }
  apply(pref);
  try { localStorage.setItem("agTheme", pref); } catch (e) {}
  window.agTheme = {
    get pref() { return pref; }, get theme() { return resolve(pref); },
    set: function (p) {
      if (!/^(classic|trendy)-(light|dark|auto)$/.test(p)) return Promise.reject(new Error("theme"));
      apply(p, true); try { localStorage.setItem("agTheme", p); } catch (e) {}
      return fetch("/api/account/theme", { method: "POST", body: JSON.stringify({ theme: p }) }).then(function (r) { return r.json(); });
    }
  };
  addEventListener("storage", function (e) { if (e.key === "agTheme" && e.newValue && e.newValue !== pref) apply(e.newValue, true); });  // 다른 탭에서 바꾸면 바로
  if (mq && mq.addEventListener) mq.addEventListener("change", function () { if (/auto$/.test(pref)) apply(pref, true); });
})();
