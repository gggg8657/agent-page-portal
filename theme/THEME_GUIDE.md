# 화면 테마 — 도구별 손질 안내 (tools/<도구>.css)

포털에서 고르는 테마는 4가지(+ 각 계열의 "컴퓨터 설정 따라 자동")입니다.

| 값 | 뜻 | 도구 화면에 붙는 것 |
|---|---|---|
| `classic-light` | 예전 화면 그대로 | **아무 CSS 도 안 붙음** (boot 스크립트만 — 화면은 바이트 단위로 같음) |
| `classic-dark` | 같은 배치, 어두운 색 | tokens · base-dark · dark.js · chrome · tools/<도구>.css |
| `trendy-light` | Pretendard, 큰 둥글기, 부드러운 그림자, 인디고 강조 | tokens · base-trendy · chrome · tools/<도구>.css |
| `trendy-dark` | 위 + 어두운 색 | tokens · base-trendy · base-dark · dark.js · chrome · tools/<도구>.css |

`classic-auto` / `trendy-auto` 는 브라우저의 `prefers-color-scheme` 에 따라 위 light/dark 중 하나로 바뀝니다.
결과는 늘 `<html data-ag-theme="classic-light|classic-dark|trendy-light|trendy-dark">` 하나로 정해지고
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
