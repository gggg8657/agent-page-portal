# 화면 테마 — 도구별 손질 안내 (tools/<도구>.css)

포털에서 고르는 테마는 6가지(3계열 × 밝게·어둡게, + 각 계열의 "컴퓨터 설정 따라 자동")입니다.

| 값 | 뜻 | 도구 화면에 붙는 것 |
|---|---|---|
| `classic-light` | 예전 화면 그대로 | **아무 CSS 도 안 붙음** (boot 스크립트만 — 화면은 바이트 단위로 같음) |
| `classic-dark` | 같은 배치, 어두운 색 | tokens · base-dark · dark.js · chrome · tools/<도구>.css |
| `trendy-light` | Pretendard, 큰 둥글기, 부드러운 그림자, 인디고 강조 | tokens · base-trendy · chrome · tools/<도구>.css |
| `trendy-dark` | 위 + 어두운 색 | tokens · base-trendy · base-dark · dark.js · chrome · tools/<도구>.css |
| `v2-light` | 따뜻한 무채색·청록 강조·1px 선·작은 둥글기·촘촘 (아래 'v2' 절) | tokens · base-v2 · chrome · tools/<도구>.css |
| `v2-dark` | 위 + 어두운 색 | tokens · base-v2 · base-dark · dark.js · chrome · tools/<도구>.css |

`classic-auto` / `trendy-auto` / `v2-auto` 는 브라우저의 `prefers-color-scheme` 에 따라 위 light/dark 중 하나로 바뀝니다.
결과는 늘 `<html data-ag-theme="classic-light|classic-dark|trendy-light|trendy-dark|v2-light|v2-dark">` 하나로 정해지고
(고른 값 그대로는 `data-ag-pref`), CSS 는 이 속성만 보면 됩니다.

## 어떻게 붙나

- 포털(portal.py `theme_head`)이 도구 첫 화면 HTML 의 `<meta charset>` 바로 뒤에 `theme/boot.js`(인라인)와 `<link>` 를 넣습니다.
  본문 그리기 전에 `html[data-ag-theme]` 가 정해지므로 깜빡임이 없습니다.
- 어둡게일 때는 dark.js 가 다 바꿀 때까지 `html[data-ag-dk=wait]` 로 body 를 잠깐 숨깁니다(최대 2.5초).
- 다른 탭에서 테마를 바꾸면 열린 도구 화면도 바로 바뀝니다(localStorage `agTheme` + 이벤트 `ag-theme`).
- 파일은 `/theme/…?v=수정시각` 으로 나가므로 고치면 새로고침만 하면 됩니다(포털 재시작 불필요). 단 **boot.js 는 포털 시작 때 읽으므로 고치면 재시작**.

## 토큰 (tokens.css)

색 — 테마마다 값이 바뀜:

| 토큰 | 쓰임 |
|---|---|
| `--ag-bg` | 페이지 바탕 |
| `--ag-surface` / `--ag-surface-2` / `--ag-surface-3` | 카드 바탕 / 입력·우물·칩 바탕 / 마우스 올림·더 진한 면 |
| `--ag-ink` / `--ag-ink-2` / `--ag-ink-3` | 본문 / 보조 글자 / 흐린 글자(설명·날짜) — 셋 다 바탕 위 4.5:1 이상 |
| `--ag-line` / `--ag-line-2` | 옅은 구분선 / 입력칸·버튼 테두리 |
| `--ag-accent` | 강조 바탕(흰 글씨 버튼). 흰 글씨 4.5:1 이상 |
| `--ag-accent-ink` | 강조색 **글자**(링크·선택된 탭). 바탕 위 4.5:1 이상 |
| `--ag-accent-soft` | 강조 옅은 바탕(선택 칩·태그) |
| `--ag-on-accent` | 강조 바탕 위 글자(#fff) |
| `--ag-brand` / `--ag-brand-ink` | 포털 남색(바탕용 / 글자용) |
| `--ag-ring` | 초점 링 색(`box-shadow:0 0 0 3px var(--ag-ring)`) |
| `--ag-ok` `--ag-warn` `--ag-bad` (+ `-soft`) | 상태 글자 / 상태 옅은 바탕 |
| `--ag-hover` | 줄·메뉴 마우스 올림 바탕 |
| `--ag-shadow-1` / `-2` / `-3` | 테두리 대신 / 카드 / 떠 있는 창 |
| `--ag-backdrop` | 대화상자 뒤 |
| `--ag-grad` | 주 버튼 그라디언트(트렌디), 클래식은 단색 |
| `--ag-glow` | 트렌디 페이지 위쪽 옅은 빛(background-image) |
| `--ag-scroll` | 스크롤바 손잡이 |

모양 — 모든 테마 같음: `--ag-sans`(Pretendard, `/theme/fonts`) `--ag-mono`, 간격 `--ag-1…--ag-8`(4px 단위),
둥글기 `--ag-r-xs 6 / sm 8 / md 10 / lg 14 / xl 18 / full`, 글자 `--ag-fs-xs…--ag-fs-2xl`, `--ag-ease` `--ag-dur`.

## 공통 CSS 가 이미 하는 것

**도구 변수 바꾸기** — 대부분 도구가 쓰는 이름은 이미 토큰으로 이어져 있습니다:
`--bg --fg --ink --muted --mut --sub --line --card --panel --surface --chip --soft` (트렌디는 `--acc --acc2` 도).
그러니 도구 CSS 가 `var(--bg)` 로 칠한 곳은 손댈 필요가 없습니다. `--paper`(내보내기 미리보기 흰 종이)는 일부러 안 바꿉니다.

**base-trendy.css** (트렌디 둘 다): body 글꼴 Pretendard + 위쪽 옅은 빛, h1~h3 자간, `button/.btn` 둥글기·누름·마우스 올림,
주 버튼(`.primary .btn-primary .go .main`) 광택+그림자, 입력칸 둥글기·초점 링·placeholder, checkbox/radio/range 강조색,
파일 선택 버튼, `.card .panel .box fieldset` 큰 둥글기+그림자, `.tabs` 선택 탭 굵게, `th` 회색 작은 글씨, 표 줄 마우스 올림,
`pre/textarea/code` 둥글기, `dialog` 둥글기·흐린 배경, 링크 밑줄 간격, 얇은 스크롤바.
버튼·입력칸 글꼴은 특이도 0(`:where`)이라 도구가 정한 글꼴(고정폭 textarea 등)이 이깁니다.

**base-dark.css + dark.js** (어둡게 둘 다): `color-scheme:dark`(기본 입력칸·스크롤바 어둡게),
그리고 dark.js 가 도구 CSS·`style=""` 의 **고정 색**을 OKLCH 명도로 바꿉니다:
- background·border 의 밝은 색 → 어두운 표면(회색이면 테마 표면 색조), 이미 어두운 바탕(남색 버튼 등)은 그대로
- color·fill 의 어두운 색 → 밝은 글자, 이미 밝은 글자(흰 글씨 버튼)는 그대로
- `color: var(--acc)` 처럼 강조색을 글자로 쓴 곳 → 밝은 강조색
- `@media (prefers-color-scheme: dark)` 안·`print` 규칙은 안 건드림
- **그림·캔버스·영상·SVG(속성과 SVG 안 `<style>` — Mermaid 등) 은 절대 안 바꿈**(반전 금지 — 색이 곧 내용)
- **문서 미리보기 '종이'**(내려받을 문서 모양 그대로 보여 주는 흰 종이)는 도구가 표시를 붙이면 안 바꿈: 종이 틀 요소에 `data-ag-paper`(그 안 `style=""` 건너뜀), 종이용 CSS 는 `<style data-ag-paper>` 로 따로(그 규칙 전체 건너뜀). base-dark 의 `mark`·`a`·`hr` 도 종이 안에선 안 붙고 `color-scheme:light`. 예: weekly-local `.preview`
- 도구에 자체 어둡게 모드(`[data-theme=dark]` 선택자)가 있으면 dark.js 는 손대지 않고 `html[data-ag-native]` 를 붙이고,
  boot.js 가 `html[data-theme=dark|light]` 를 붙여서 도구 것이 켜집니다(chart-local, battery-local 등). classic-light 일 때는 `data-theme` 를 안 붙임.

**chrome.css**: 포털이 끼워 넣는 `?` 사용법 버튼·사용법 창·작업 끝 알림.

## tools/<도구>.css 쓰는 법

공통으로 안 되는 곳만 고칩니다. 흔한 경우:
- 캔버스·차트 라이브러리가 JS 로 그리는 색(축·격자 글씨) — 캔버스는 못 바꾸니 틀(바탕)만 맞추거나 그대로 둠
- `style=""` 아닌 JS 가 직접 넣은 SVG `fill="#fff"` 속성
- dark.js 의 명도 뒤집기가 어색한 곳(색 의미가 있는 배지·하이라이트, 겹친 반투명)
- 도구 고유 클래스의 카드·탭·툴바를 트렌디 모양으로 (`.card` 아닌 이름: `.pane .sec .result .tile` 등)

규칙:
1. **CSS 만.** HTML·JS·DOM·기능은 그대로. 도구 ui.html 은 고치지 않습니다(이 파일만).
2. 모든 선택자는 테마 속성으로 시작: 
   - 트렌디 둘 다 `html[data-ag-theme^="trendy"] …`
   - 어둡게 둘 다 `html[data-ag-theme$="dark"] …`
   - 하나만 `html[data-ag-theme="trendy-dark"] …`
   - 클래식 밝게(`classic-light`)용 규칙은 쓰지 않습니다 — 이 파일 자체가 안 붙습니다.
3. 색은 토큰으로(`var(--ag-…)`). 새 hex 는 꼭 필요할 때만(분류색·차트 계열색).
4. 자체 어둡게 모드가 있는 도구는 그 모드를 존중: 어둡게 고칠 것은 `html[data-ag-native][data-ag-theme$="dark"]` 로.
5. 배치(너비·그리드·순서·display)는 바꾸지 않습니다. 둥글기·그림자·간격 조금·글꼴·색만.
6. `!important` 는 도구 인라인 style 을 이겨야 할 때만.
7. 그림·영상·캔버스에 `filter:invert()` 금지. 밝기 낮추기도 하지 않음.

예:
```css
/* mail-local — 결과 미리보기 카드, 목적 칩 */
html[data-ag-theme^="trendy"] .out{border-radius:var(--ag-r-lg);box-shadow:var(--ag-shadow-2)}
html[data-ag-theme^="trendy"] .chips button.on{background:var(--ag-accent-soft);color:var(--ag-accent-ink);border-color:transparent}
html[data-ag-theme$="dark"] .diff ins{background:var(--ag-ok-soft);color:var(--ag-ok)}
```

## 확인 순서 (체크리스트)

- [ ] 테스트 포털(:8701)에서 4가지 테마로 도구 첫 화면 + 결과가 나온 화면(가능하면 한 번 실행) 캡처, 1440px
- [ ] classic-light 가 예전과 같음(이 파일은 안 붙으므로 공통 파일을 고친 경우만 확인)
- [ ] 본문 글자 대비 4.5:1, 큰 글자·아이콘 3:1 (흐린 글자는 `--ag-ink-3` 이상)
- [ ] 입력칸·선택칸·체크박스·파일 선택·드롭다운이 어둡게에서 읽힘(placeholder 포함)
- [ ] 탭·버튼 눌린 상태·비활성(disabled) 구분
- [ ] 그림·캔버스·미리보기 원래 색 그대로
- [ ] 콘솔 오류 없음, 도구 자체 어둡게 토글(있으면) 계속 됨
- [ ] 다른 탭에서 테마를 바꾸면 이 화면도 바뀜(새로고침 없이)

캡처는 쿠키 `agtheme=<테마>` 만 넣고 127.0.0.1 로 열면 됩니다(로그인 없이 서버 주인).

## v2 (2026-10-13 추가) — 도구 css 에 v2 규칙 쓰기

방향(자세히는 scratchpad `v2/V2_BRIEF.md`): **작업대** — Linear·Vercel(2026 사이드바 개편) 류의 "도구 틀은 물러서고 내용이 앞으로".
따뜻한 무채색(돌 회색) 바탕 + **청록 강조 하나**, **1px 선**으로 구분(그림자는 떠 있는 창만), **작은 둥글기 4~8px**, 촘촘한 줄(30~32px), 숫자는 고정폭(tnum).
trendy(큰 둥글기·부드러운 그림자·인디고 그라디언트·알약 칩)와 반대로 간다 — 그라디언트·광택·떠오르는 카드·알약 모양 쓰지 않기.

**선택자**
- v2 둘 다 `html[data-ag-theme^="v2"] …` · 하나만 `html[data-ag-theme="v2-dark"] …`
- 어둡게 공통 규칙(`$="dark"`)은 v2-dark 에도 걸린다 — 기존 어둡게 손질은 그대로 v2-dark 에 쓰임(dark.js 가 v2 표면 색조로 바꿈)
- trendy 규칙(`^="trendy"`)은 v2 에 안 걸린다. trendy 에서 한 모양 손질(둥글기·그림자)을 v2 에도 원하면 **v2 용으로 따로**(값은 작게·선으로)

**토큰** — 이름은 같고 값만 v2 (tokens.css):

| 토큰 | v2-light | v2-dark |
|---|---|---|
| bg / surface / surface-2 / surface-3 | #f7f6f3 / #fff / #f1f0ec / #e8e6e1 | #141412 / #1b1b19 / #22221f / #2b2b28 |
| `--ag-side` (v2 만: 왼쪽 메뉴 바탕) | #f1f0ec | #111110 |
| ink / ink-2 / ink-3 | #1c1b18 / #474540 / #67645d (5.5:1) | #ecebe7 / #bcb9b2 / #9d9991 (6.5:1) |
| line / line-2 | #e6e3dd / #d3cfc7 | #2b2a27 / #3b3a36 |
| accent (흰 글씨) / accent-ink (글자) / accent-soft | #0e7268 (5.8:1) / #0b665d / #e2f1ee | #0d7a70 (5.2:1) / #5ccfbf / #173430 |
| ok / warn / bad | #146c36 / #94580a / #c42b2b | #5ccf88 / #efb453 / #f27a72 |
| shadow-1 / -2 / -3 | 없음 / 아주 옅게 / 1px 테두리+떠 있는 그림자(창·팝업만) | 같은 구조 |
| `--ag-grad` | = accent (단색) | = accent |

모양 토큰도 v2 에서만 값이 바뀐다: `--ag-r-xs 4 / sm 5 / md 6 / lg 8 / xl 10`, 글자 `--ag-fs-xs 11 … --ag-fs-md 13 … --ag-fs-2xl 22`, `--ag-row 32px`.
그러니 **둥글기·글자 크기는 `var(--ag-r-*)`·`var(--ag-fs-*)` 로 쓰면 trendy·v2 가 알아서 다르게** 나온다. 숫자를 직접 쓰지 말 것.

**base-v2.css 가 이미 하는 것**: 공통 변수(`--bg --fg --ink --muted --line --card --panel --chip --soft --acc --acc2` …)→토큰, 글꼴 Pretendard+tnum,
버튼·입력 작은 둥글기·초점 링(강조 테두리+3px 링), 주 버튼(`.primary .btn-primary .go .main`) 단색(그라디언트·광택 제거), 파일 선택 버튼,
`.card .panel .box fieldset` 둥글기 8px·그림자 없음, `.tabs` 선택 탭 굵게, `th` 작은 회색, 줄 마우스 올림, 대화상자 10px+떠 있는 그림자, 얇은 스크롤바.

**도구 css 에서 v2 로 손볼 것(흔한 경우)**
- 도구 고유 이름의 카드·타일·툴바(`.pane .sec .result .tile` …): `border:1px solid var(--ag-line);border-radius:var(--ag-r-lg);box-shadow:none;background:var(--ag-surface)`
- 알약 칩·세그먼트: 둥글기 `var(--ag-r-sm)`, 고른 것 `background:var(--ag-accent-soft);color:var(--ag-accent-ink)`
- 도구가 자체로 넣은 그라디언트·큰 그림자 → 단색·선으로
- 강조색이 도구 고유 파랑/보라로 하드코딩된 곳 → `var(--ag-accent)` / 글자는 `var(--ag-accent-ink)`
- 목록·표 줄 높이: 가능하면 `min-height:var(--ag-row)` 정도로 촘촘하게(배치는 바꾸지 않기)

```css
/* 예: mail-local */
html[data-ag-theme^="v2"] .out{border:1px solid var(--ag-line);border-radius:var(--ag-r-lg);box-shadow:none}
html[data-ag-theme^="v2"] .chips button{border-radius:var(--ag-r-sm)}
html[data-ag-theme^="v2"] .chips button.on{background:var(--ag-accent-soft);color:var(--ag-accent-ink);border-color:color-mix(in srgb,var(--ag-accent) 40%,transparent)}
```

**v2 체크리스트**
- [ ] 테스트 포털(:8701)에서 `agtheme=v2-light` · `v2-dark` 쿠키로 첫 화면 + 결과 화면, 1440 · 390
- [ ] classic·trendy 4가지가 **고치기 전과 픽셀 같음**(공통 파일을 안 건드렸으면 도구 파일 v2 규칙은 다른 테마에 안 걸림 — 선택자가 모두 `^="v2"` 인지 확인)
- [ ] 그라디언트·광택·큰 그림자·알약 모양이 v2 에 남지 않음(trendy 규칙이 안 걸리니 도구 자체 것만 보면 됨)
- [ ] 강조는 청록 하나 — 도구 고유 파랑·보라 강조가 남았으면 토큰으로(분류색·차트 계열색은 그대로)
- [ ] 대비 AA, 그림·캔버스·문서 '종이'(`data-ag-paper`) 색 그대로, 콘솔 오류 0

**포털 화면(참고)**: v2 일 때 포털은 `portal.css` 대신 `portal-v2.css` 를 붙이고, 홈·계정 화면의 마크업을 `v2/home.html` · `v2/account.html` 로 바꿔 내보낸다(로그인 랜딩 `login.html` 은 모든 테마가 한 배치 — 자기 `<style>` 안에서 `--ag-*` 토큰으로 색·모양만 바꿈, classic-light 는 예전 로그인 색)
(portal.py `v2_page`: 원래 파일의 style 부터 첫 script 앞까지를 교체, **스크립트는 원래 것 그대로** — id·폼 이름을 바꾸면 안 됨, selftest 가 확인).
v2 ↔ 다른 계열로 바꾸면 포털 화면은 저장 뒤 새로고침(boot.js `agTheme.set`, 다른 탭은 storage 이벤트로). 도구·설명서 화면은 새로고침 없이 바로 바뀜.
v2 포털 화면에는 `palette-v2.js`(Ctrl+K 명령 팔레트)가 붙는다. 설명서(/guide) v2 손질은 `guide.css` 끝 v2 절(화면 전용, 인쇄는 원래 모양).

**첫 로그인 둘러보기**(`theme/tour.js`): 포털 홈(classic·trendy·v2 공통)에만 붙는다. 색은 `--ag-*` 토큰(없으면 classic-light 값), 비출 요소는 파일 안 `TARGETS` 표(레이아웃별 선택자). 홈 배치를 바꿔 `.search`·`#chips`·`#cols`·`#user`·`#agHelpBtn` 이 없어지면 그 단계는 가운데 말풍선으로 뜬다 — 바꿀 때 표도 같이 고칠 것.
