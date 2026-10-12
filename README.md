# agent-page-portal — 에이전트 페이지 포털

> **한 줄 요약** — 같은 상위 폴더에 나란히 둔 로컬 LLM 도구 25개(목록 노출 24개)를 한 화면에 모은 포털. 검색·카테고리로 도구를 찾고, `/t/<도구>/` 프록시로 UI를 열고, README·사용법 팝업·시작/중지·사용 통계를 한곳에서 다룹니다. 파이썬 표준 라이브러리만.
> 각 도구는 **따로따로 별도 저장소**로 공개되어 있고, 이 저장소는 그 도구들을 묶어 띄우는 포털입니다.

> ⚠️ 연구·내부 시험용 프로토타입입니다(배포·상업 서비스용 아님). 제3자 구성요소의 라이선스는 THIRD_PARTY_NOTICES.md 를 확인하세요.

![포털 홈 — 검색, 카테고리 칩, 인기·신규 요약, 내 에이전트 만들기](docs/img/home.png)

## 사용 방법
번호는 화면의 번호 상자와 같습니다. 처음 들어오면 **로그인** 화면이 뜹니다 — '처음이에요'에서 아이디(영문 소문자·숫자)·이름·부서·PIN(숫자 4~8자리)으로 가입하면 바로 들어갑니다. PIN 은 서버에 해시로만 저장되고, 잊으면 관리자가 초기화합니다.

1. **검색**(홈 ①) — 도구 이름·설명·태그를 한 번에 찾습니다(예: 회의록, HWP, 그림).
2. **카테고리 칩**(홈 ②) — 문서·회의·그림·음성·데이터·재미·토이·연구·원자력으로 목록을 좁힙니다.
3. **홈 요약**(홈 ③) — 인기 TOP·신규·최근 사용 카드에서 바로 고릅니다. 사용자 랭킹은 계정 이름으로 표시됩니다(IP 는 보이지 않음).
4. **내 에이전트 만들기**(홈 ④) — 새 도구 등록 안내로 연결됩니다. 오른쪽 위 **📘 사용 설명서**(홈 ⑤)는 도구마다 쓰는 순서를 화면으로 정리한 설명서(`/guide/`, PDF 내려받기)를 엽니다.
5. **전체 에이전트** 목록에서 정렬(이름순·최신순, 아래 ①)과 카테고리 필터로 카드를 고르고 **열기**(아래 ②) — 또는 `http://<포털>:8700/t/<도구폴더>/` 로 바로 갑니다.
6. 카드의 **README**는 저장소 설명을, 도구 화면 오른쪽 위 **?**(아래 ①)는 `guides/<도구폴더>.md` 짧은 사용법(②)을 열고, **설명서에서 보기**(③)는 설명서의 그 도구 부분으로 갑니다.

<p>
<img src="docs/img/list.png" alt="전체 목록 — 정렬·필터·카드" width="49%">
<img src="docs/img/readme.png" alt="README 모달" width="49%">
</p>

![도구 화면의 ? 사용법 팝업](docs/img/help.png)

## 예시
SQL 도구로 프록시 진입(같은 세션 캡처): `http://localhost:8700/t/sql-local/` → demoDB(가상 데이터)에 질문 "부서별 과제 예산 합계를 큰 순서로" →

```sql
SELECT "부서", SUM("예산") AS "예산합계"
FROM "연구과제" GROUP BY "부서"
ORDER BY "예산합계" DESC LIMIT 500
```

5행 · gemma4:31b · 표·SVG 차트 생성. 각 도구는 자체 `app.py`·`ui.html` 로 동작하고, SSE·파일 다운로드도 포털이 그대로 전달합니다.

## 포털이 하는 일
- **목록·검색** — `tools.json`(요청마다 다시 읽음)에 적힌 도구를 카드로 보여 줌. "+ 내 에이전트 만들기"로도 추가. `"hidden": true` 면 목록에서 숨김(프록시는 됨).
- **시작/중지** — 각 도구 폴더의 `setup.sh` 를 `PORT`·`WORKSPACE` 를 넘겨 실행. `python3 portal.py start-all` / `stop-all`.
- **단일 진입점(프록시)** — 모든 도구를 `http://<포털>:8700/t/<도구폴더>/` 로 리버스 프록시(SSE·파일 다운로드 포함). 포트 하나만 열면 되고, 도구 UI 는 상대경로만 씀. 도구는 `127.0.0.1` 에만 열려 있어 직접 포트로는 접속할 수 없음.
- **사용법 팝업** — 프록시하는 도구 첫 화면에 `guides/<도구폴더>.md` 를 오른쪽 위 **?** 팝업으로 붙임(포털 자체는 `guides/portal.md`).
- **로그인** — 계정은 `portal.db` 의 `users`(아이디·이름·부서·PIN 의 pbkdf2-sha256 해시 + 사용자별 salt, 60만 회 — 예전 20만 회 계정은 다음 로그인 때 자동으로 다시 해시). 세션은 무작위 32바이트 토큰(DB 에는 sha256 만), 쿠키 `agsid`(HttpOnly·SameSite=Lax, 30일, 쓰면 연장). 로그인 실패는 아이디당 10분에 5번까지. 로그인 안 한 화면 요청은 `/login?next=…` 로, API 는 401. 접속 IP 는 NAT 뒤라 모두 서버 주소로 보이므로 사람 구분에 쓰지 않습니다 — 단, 이 서버 안(127.0.0.1)에서 쿠키 없이 온 요청(SSH 터널 테스트 포털·내부 호출)은 서버 주인으로 봅니다(무효 쿠키가 있으면 다시 로그인). 계정 만들기·PIN 초기화: `python3 portal.py --add-user <id> --name <이름> [--dept 부서] [--pin 숫자]`(PIN 을 안 주면 무작위 6자리를 화면에만 출력).
- **통계·피드** — `AGENT_DATA/portal.db` sqlite 하나(WAL)에 열기·좋아요·피드 글·사용자 랭킹(계정 id 기준, 랭킹은 이름으로 표시). 즐겨찾기·최근 사용·펫 상태는 브라우저 localStorage(펫·사용법 팝업은 계정별 키).
- **계정·부서·권한** — 역할은 관리자(`AGENT_OWNERS` 계정) · 부서장(부서마다, 여럿 가능) · 일반. 오른쪽 위 **계정 설정**(`/account`)에서 이름 바꾸기, 부서 소속 요청, 부서장 권한 요청, PIN 바꾸기, 내 요청 보기·취소.
  - 부서는 관리자가 만든 목록에서만 고름(`depts` — 처음엔 기존 계정·`tools.json` 의 부서로 채움). 목록에 없으면 관리자에게 요청.
  - 부서 소속(가입 때 고른 부서 포함)은 **요청 → 그 부서 부서장 승인**(부서장이 없으면 관리자) 뒤에 반영, 그 전엔 '승인 대기'. 대기 요청은 종류마다 하나. 다른 부서로 옮기면 예전 부서의 부서장 권한은 내려놓음.
  - 부서장 권한은 **관리자에게** 요청 → 승인. 관리자는 직접 지정·회수도 함.
  - 승인할 사람은 위쪽 막대의 **요청 N** 배지와 화면 알림으로 알고 **승인**(`/approvals`)에서 승인·거절(메모 선택). 요청한 사람은 다음 화면을 열 때 결과 알림을 받음.
  - **부서장이 할 수 있는 것**: 자기 부서 소속 요청 승인·거절, 자기 부서 구성원 목록과 각자 포털에서 도구를 연 횟수(최근 30일·누적)·마지막 접속 보기. 그 밖에는 일반 사용자와 같고, 도구 쪽 권한·기록 공유는 바뀌지 않음.
  - **관리**(`/admin`, 관리자만): 계정 목록(부서·역할·가입·마지막 접속), 부서 추가·이름 바꾸기·숨기기, 부서장 지정·회수, 모든 대기 요청, PIN 초기화(무작위 6자리를 한 번만 보여 줌), 계정 정지·해제, 감사 기록.
  - API 는 `/api/account/*` · `/api/approvals/*` · `/api/admin/*` — 모든 호출에서 서버가 역할을 확인(아니면 403). 승인·거절·부서장 지정/회수·PIN 초기화·정지·부서 변경은 `audit` 표에 남김.
- **사용자 구분** — 도구로 넘길 때 `X-Agent-User` 헤더를 포털이 붙임(클라이언트가 보낸 `X-Agent-User`·`X-Forwarded-User`·`X-User` 와 포털 세션 쿠키는 버림): 주인 계정(`AGENT_OWNERS`)은 `owner`, 나머지는 계정 id. 도구는 이 값으로 기록을 사용자별 폴더에 나눠 저장하고, 포털은 도구를 `BIND=127.0.0.1` 로 띄워 포털을 거쳐서만 열리게 함. `/api/me` → `{id, name, owner, role, dept, dept_pending, heads, approvals}`.
- **작업 끝 알림** — 1분 넘게 걸린 도구 요청이 끝나면 그 사용자의 포털·도구 화면(어느 탭이든)에 알림(https·localhost 면 브라우저 알림, 아니면 화면 왼쪽 아래 알림 + 탭 제목 깜빡임). `/api/events` 는 자기 작업만(주인은 전부). 끄기: **?** 사용법 창 아래 '작업 끝 알림'.
- **GPU 현황** — 홈 아래에 GPU별 메모리·사용률과 어느 도구가 쓰는지(`/api/gpu`, 5초 캐시, 10초마다 새로고침). 컨테이너 안에서는 `nvidia-smi` 의 pid 가 호스트 pid 라 생성 순서·GPU 집합으로 짝지은 **추정**입니다.
- **백업** — `backup.sh`: `_data` 전체(캐시·모델·가상환경 제외, sqlite 는 backup API 로 일관된 사본)를 `~/agent-page-backup/agent-data-<날짜>.tar.gz` 로, 최근 7일치만 남김(로그 `backup.log`). 운영 포털(:8700)이 매일 03시(KST)쯤 부르고, `autostart.sh` 가 재시작 직후 한 번 부름. 되살리기: `tar xzf <파일> -C <상위폴더>`. 위치 `AGENT_BACKUP_DIR`, 개수 `BACKUP_KEEP`.
- **점검** — `bash audit.sh`: 도구별 CDN 유출·포트 중복·LICENSE/NOTICE·selftest.

## 연결된 도구 (25개, `tools.json` 기준)

| 이름 | 폴더 | 포트 | 분류 | 하는 일 | 저장소 | 목록 |
|---|---|---|---|---|---|---|
| Humanize KR | `humanize-kr-local` | 8765 | 문서 | AI가 쓴 한글의 번역투·상투구 제거 | [gggg8657/humanize-kr-local](https://github.com/gggg8657/humanize-kr-local) | 보임 |
| kordoc | `kordoc-local` | 8766 | 문서 | HWP·PDF 요약·질문·공문서 초안 HWPX | [gggg8657/kordoc-local](https://github.com/gggg8657/kordoc-local) | 보임 |
| Notebook | `notebook-local` | 8769 | 문서 | 출처 올리고 인용 채팅·노트·팟캐스트 대본 | [gggg8657/notebook-local](https://github.com/gggg8657/notebook-local) | 보임 |
| Writer | `writer-local` | 8773 | 문서 | 건배사·경조사·축사·공지문 패턴 글쓰기 | [gggg8657/writer-local](https://github.com/gggg8657/writer-local) | 보임 |
| Meeting | `meeting-local` | 8767 | 회의 | 녹음 → 화자 분리 → 회의록·인사이트 | [gggg8657/meeting-local](https://github.com/gggg8657/meeting-local) | 보임 |
| Diagram | `diagram-local` | 8768 | 그림 | 말로 설명하면 다이어그램 | [gggg8657/diagram-local](https://github.com/gggg8657/diagram-local) | 보임 |
| Sketch | `sketch-local` | 8774 | 그림 | 손그림 사진 → 논문 figure 스타일 다이어그램 | [gggg8657/sketch-local](https://github.com/gggg8657/sketch-local) | 보임 |
| TTS | `tts-local` | 8771 | 음성 | 한국어 음성 합성 (OpenAI 음성 API 호환) | [gggg8657/tts-local](https://github.com/gggg8657/tts-local) | 보임 |
| SQL (demoDB) | `sql-local` | 8772 | 데이터 | 자연어 → SQL → 표·차트 · **기본 DB 는 가상(합성) demoDB** — 실제 DB 는 운영자가 연결 | [gggg8657/sql-local](https://github.com/gggg8657/sql-local) | 보임 |
| 팀 밸런스 맵 | `saju-local` | 8775 | 재미 | 사주·MBTI 협업 궁합 (워크숍용) | [gggg8657/saju-local](https://github.com/gggg8657/saju-local) | 보임 |
| Persona | `persona-local` | 8776 | 토이 | 기억·호감도 캐릭터 채팅 + 음성 답변·말로 대화·립싱크 클립 | [gggg8657/persona-local](https://github.com/gggg8657/persona-local) | 숨김 |
| Avatar | `avatar-local` | 8777 | 그림 | 가상 캐릭터 그림 → 말하기(Wan2.2-S2V)·움직이기(Wan2.2-I2V) 영상, 목소리는 tts-local | [gggg8657/avatar-local](https://github.com/gggg8657/avatar-local) | 보임 |
| CAD Agent | `agent-cad-local` | 8778 | 그림 | 말하면 3D 부품·2D 판재·ICT 배치도/개황도, 기존 DXF 수정 | [gggg8657/agent-cad-local](https://github.com/gggg8657/agent-cad-local) | 보임 |
| 상장 생성기 | `award-local` | 8779 | 문서 | 상장·표창장·감사장 — 디자인 7종, PDF/PNG, 일괄 발급 | [gggg8657/award-local](https://github.com/gggg8657/award-local) | 보임 |
| 메일 | `mail-local` | 8780 | 문서 | 업무 메일 작성·회신·윤문 (diff·숫자 보존 검사) | [gggg8657/mail-local](https://github.com/gggg8657/mail-local) | 보임 |
| 단백질 구조 예측 | `protein-local` | 8781 | 연구 | 서열 → 3D 구조·pLDDT·PAE, 결합 친화도 순위, 서열 설계 (Boltz-2 + ProteinMPNN, 폐쇄망) | [gggg8657/protein-local](https://github.com/gggg8657/protein-local) | 보임 |
| 그래프 디지타이저 | `digitizer-local` | 8783 | 데이터 | 그래프 그림·PDF → 숫자 데이터 (축 AI 보정·색 추출·CSV/엑셀) | [gggg8657/digitizer-local](https://github.com/gggg8657/digitizer-local) | 보임 |
| 논문 리뷰 | `review-local` | 8782 | 문서 | 투고 전 원고 사전 심사 — 인용 확인된 심사 지적·예상 판정·예상 질문 + 그림·인용·약어·수치 기계 점검 | [gggg8657/review-local](https://github.com/gggg8657/review-local) | 보임 |
| 그래프 | `chart-local` | 8786 | 데이터 | 표 → 그래프 후보 여러 개 → 고칠 수 있는 XLSX·PPTX·DOCX·HWPX 차트 | [gggg8657/chart-local](https://github.com/gggg8657/chart-local) | 보임 |
| 차폐 계산기 | `shield-local` | 8784 | 원자력 | 방사선 차폐·선량 간이 계산 — 점선원 감마, 축적인자, 필요 두께 역산, 누적선량 | [gggg8657/shield-local](https://github.com/gggg8657/shield-local) | 보임 |
| 배터리 | `battery-local` | 8785 | 연구 | PyBaMM 배터리 성능·수명(열화) 예측 — C-rate·충전·SOH·EOL·LLI/LAM | [gggg8657/battery-local](https://github.com/gggg8657/battery-local) | 보임 |
| 주간보고 | `weekly-local` | 8788 | 문서 | 주간보고 개인 작성·실 취합 — 핵심 주황·과기부 파랑·BBS 취소선, 약어 풀이, HWPX·DOCX | [gggg8657/weekly-local](https://github.com/gggg8657/weekly-local) | 보임 |
| 파동이 | `padong-local` | 8789 | 토이 | KAERI 캐릭터 파동이 3D·2D — 말로 시키면 움직이고 말함, 날짜별 옷, 포털 모든 화면을 돌아다니는 펫 | [gggg8657/padong-local](https://github.com/gggg8657/padong-local) (비공개 — 캐릭터 권리는 KAERI·허쉬위쉬) | 보임 |
| 그림 생성 | `image-local` | 8792 | 그림 | 말로 그리고 말로 고치는 범용 그림 도구 — Qwen-Image-2512 · Qwen-Image-Edit-2509 | [gggg8657/image-local](https://github.com/gggg8657/image-local) | 보임 |
| 글쓰기 코치 | `coach-local` | 8798 | 문서 | 쓰는 동안 공문·편지·논문·메일 등 글 종류(페르소나)·목적에 맞춰 고칠 곳을 준실시간으로 제안 | [gggg8657/coach-local](https://github.com/gggg8657/coach-local) | 보임 |

'목록'은 `tools.json` 의 `hidden` 값입니다(현재 값 그대로). 숨긴 도구는 일반 포털 목록에서만 빠지고, 테스트 포털(`SHOW_HIDDEN=1`)에서는 보입니다.

## 도구가 연결되는 방식

```
브라우저 ──▶ portal.py :8700 ──/t/<dir>/──▶ 127.0.0.1:<port> (각 도구 app.py)
                │  시작할 때 넘기는 환경변수: PORT, WORKSPACE=$AGENT_DATA/<dir>, 그리고 포털이 받은 환경 전부
                └─ 첫 화면 HTML 에 guides/<dir>.md 사용법 팝업(?) 삽입
도구들 ──▶ LLM_BASE_URL (로컬 Ollama, 기본 gemma4:31b)
persona / notebook / avatar ──▶ TTS_BASE_URL(tts-local) · STT_BASE_URL(meeting-local) · AVATAR_URL(avatar-local)
```

- **데이터 한곳에** — `AGENT_DATA`(기본 `../_data`) 아래에 `portal.db` 와 각 도구의 작업 폴더(`_data/<도구>/…`)가 모입니다. 포털이 도구를 띄울 때 `WORKSPACE=$AGENT_DATA/<도구>` 를 넘기고, 모든 도구가 그 변수를 읽습니다. 백업은 이 폴더 하나. (이 폴더는 어느 저장소에도 올리지 않습니다.)
- **LLM** — `start-portal.sh` 가 `LLM_API=ollama LLM_BASE_URL=http://127.0.0.1:11436 LLM_MODEL=gemma4:31b MODEL=… VISION_MODEL=…` 를 export 하고 포털이 그대로 도구에 물려 줍니다. 모든 도구는 OpenAI 호환/Ollama API 로 이 주소를 부릅니다(외부 인터넷 불필요).
- **도구끼리 연결** — `start-portal.sh` 가 `TTS_BASE_URL=http://127.0.0.1:8771/v1`(tts-local), `STT_BASE_URL=http://127.0.0.1:8767/v1`(meeting-local 받아쓰기), `AVATAR_URL=http://127.0.0.1:8777/api/run`(avatar-local 립싱크) 와 `TTS_VOICE/TTS_MODEL/…_DEVICE` 를 넘깁니다. 그 밖에 chart·review·weekly·meeting 은 옆 폴더 `../kordoc-local` 의 kordoc 을 (있으면) 불러 HWP/HWPX 를 처리합니다.

## 전체 설치 — 도구를 나란히 clone

포털은 `<상위폴더>/<도구>/` 구조를 전제로 하므로 모든 저장소를 같은 상위 폴더에 clone 합니다.

```bash
mkdir agent-page && cd agent-page
git clone https://github.com/gggg8657/agent-page-portal
bash agent-page-portal/scripts/clone-all.sh     # tools.json 의 도구 전부 clone(있으면 pull) + portal 심볼릭 링크
```

손으로 하려면: `for d in humanize-kr-local kordoc-local notebook-local writer-local meeting-local diagram-local sketch-local tts-local sql-local saju-local persona-local avatar-local agent-cad-local award-local mail-local protein-local digitizer-local review-local chart-local shield-local battery-local weekly-local image-local coach-local; do git clone https://github.com/gggg8657/$d; done`

그다음 각 도구의 README 대로 의존성(`bash <도구>/setup.sh`)과 로컬 Ollama(+ `ollama pull gemma4:31b`)를 준비합니다.

## 실행

```bash
bash agent-page-portal/scripts/start-portal.sh        # 운영 포털 :8700 (모든 주소에서 열림) — Ollama 확인 → LLM·도구 연결 환경변수 export → portal.py
bash agent-page-portal/scripts/start-test-portal.sh   # 본인 테스트 포털 :8701, HOST=127.0.0.1, SHOW_HIDDEN=1 (숨긴 도구도 보임, SSH 터널로 접속)
bash agent-page-portal/scripts/autostart.sh           # 재부팅 후 한 번: 포털 + tools.json 의 모든 도구 기동 + 백업 (~/.zshrc 에서 백그라운드 호출용)
bash agent-page-portal/scripts/backup.sh              # 지금 백업 한 번 (_data → ~/agent-page-backup, 7일치)
python3 portal.py                 # 포털만 직접 — http://localhost:8700
python3 portal.py start-all       # tools.json 의 설치된 도구 전부 기동 (각 폴더 setup.sh, PORT 전달)
python3 portal.py stop-all
bash audit.sh                     # 전체 점검: CDN 유출·포트 중복·LICENSE/NOTICE·selftest
```

| 환경변수 | 기본 | 설명 |
|---|---|---|
| `PORT` | 8700 | 포털 포트 (테스트 포털 8701) |
| `HOST` | (모든 주소) | `127.0.0.1` 이면 서버 안에서만 열림 |
| `SHOW_HIDDEN` | 없음 | `1` 이면 `hidden: true` 도구도 목록에 |
| `AGENT_DATA` | `../_data` | 포털 DB + 모든 도구 작업 폴더 루트 |
| `TLS_CERT` / `TLS_KEY` | 없음 | 둘 다 있으면 https(TLS 1.2 이상)로 열고 세션 쿠키에 `Secure` — 아래 'https (선택)' |
| `AGENT_OWNERS` | dongju,gggg8657 | 쉼표 목록 — 이 계정은 도구에 `owner`(예전 기록 포함 전부 보임). 서버 밖에서는 이 아이디로 가입 못 함 |
| `BACKUP` | :8700 이면 1 | `1` 이면 포털이 매일 03시(KST) `backup.sh` 실행 |
| `AGENT_BACKUP_DIR` / `BACKUP_KEEP` | `~/agent-page-backup` / 7 | 백업 위치·보관 개수 (`backup.sh` 가 읽음) |
| `OLLAMA_PORT` / `LLM_MODEL` | 11436 / gemma4:31b | `scripts/start-*.sh` 가 읽음 |

`scripts/` 의 네 스크립트는 원래 상위 폴더(`agent-page/`)에 있던 `start-portal.sh` · `start-test-portal.sh` · `autostart.sh` · `backup.sh` 의 사본입니다(상위 폴더로 `cd` 하도록만 바꾸고, 서버 주소는 `<server-ip>` 로 가림). 상위 폴더 쪽 원본도 그대로 쓸 수 있습니다.
`~/.local/ollama-gpu/start.sh` 는 작성자 서버의 Ollama 기동 스크립트로, 없으면 건너뜁니다 — Ollama 는 각자 띄우세요.

### https (선택, 기본은 꺼짐)
내부망이라도 PIN·세션 쿠키가 평문으로 흐르지 않게 하려면 사내용 CA 를 만들어 인증서를 붙입니다(인터넷 필요 없음). 켜면 브라우저 작업 끝 알림(시스템 알림)도 쓸 수 있습니다.

```bash
mkdir -p _data/tls && cd _data/tls && umask 077
openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -keyout ca.key -out ca.crt -subj "/CN=KAERI Agent Portal CA"     # 사내 CA (ca.key 는 밖에 내보내지 말 것)
openssl req -newkey rsa:2048 -nodes -keyout portal.key -out portal.csr -subj "/CN=<server-ip>"
printf "subjectAltName=IP:<server-ip>\nextendedKeyUsage=serverAuth\n" > san.ext                                            # 도메인 이름이 있으면 DNS:<이름> 도
openssl x509 -req -in portal.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -out portal.crt -extfile san.ext
```

- 포털을 `TLS_CERT=$PWD/_data/tls/portal.crt TLS_KEY=$PWD/_data/tls/portal.key` 로 띄웁니다(`start-portal.sh` 의 `python3 portal.py` 앞에 붙이기). 주소는 `https://<server-ip>:8700`.
- 각 PC 에 `ca.crt` 하나만 나눠 신뢰시킵니다 — Windows: 더블클릭 → 인증서 설치 → 로컬 컴퓨터 → '신뢰할 수 있는 루트 인증 기관'. macOS: 키체인 접근 → 시스템 → 항상 신뢰.
- 켜면 `http://` 주소는 더 이상 안 열립니다(같은 포트). 키 파일은 git 에 넣지 말 것(`_data/` 는 저장소 밖).

## 출처·감사 (Credits)

- 포털 서버는 파이썬 표준 라이브러리만 씁니다. 제3자 구성요소 전체 목록·라이선스·상업 이용 여부는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 에 있습니다. 연결된 각 도구의 출처는 각 저장소의 `THIRD_PARTY_NOTICES.md`·README 'Credits' 를 보세요.
- 기본 LLM: [Ollama](https://github.com/ollama/ollama) (MIT) + Google [Gemma](https://ai.google.dev/gemma) `gemma4:31b` (Apache-2.0) — 포털은 환경변수로 넘겨 줄 뿐 동봉하지 않습니다.
- 사용 설명서(`/guide`): 글꼴 [Noto Sans KR](https://fonts.google.com/noto/specimen/Noto+Sans+KR) (SIL OFL 1.1, Google Fonts), 캡처·PDF 생성에 [Playwright](https://github.com/microsoft/playwright) (Apache-2.0)·[Pillow](https://github.com/python-pillow/Pillow) (MIT-CMU).
- 상업 이용 확인이 필요한 항목(캐릭터 파동이 그림, 생성 얼굴, 연결 도구의 비상업 요소)은 [#1](https://github.com/gggg8657/agent-page-portal/issues/1) 에 모았습니다.

저작권 표기는 `NOTICE` 를 보세요.

All rights reserved. 저작자의 허락 없이 복제·배포·개작할 수 없습니다.

### GPU 나눠 쓰기
GPU 를 도구마다 미리 정해 두지 않습니다. 포털 기동 스크립트는 모든 GPU(`CUDA_VISIBLE_DEVICES=0,1,2,3` — 셸에 다른 값이 있어도 덮어씀, 줄이려면 `AGENT_GPUS=2,3`)를 도구에 넘기고, 각 도구가 **모델을 올리는 그 순간 여유 메모리가 가장 큰 GPU 1장**(같으면 덜 바쁜 쪽)을 고릅니다. 각 도구 저장소의 `gpu_pick.py`(stdlib, 같은 파일 복사)가 `nvidia-smi` 로 고릅니다.

| 도구 | 언제 고르나 | 다 쓰면 |
|---|---|---|
| protein-local | 예측·설계 작업마다 | 작업 끝나면 프로세스 종료 |
| avatar-local 스튜디오(Qwen-Image-2512 약 60GB · Wan2.2 I2V-A14B 약 48GB) | 모델을 올릴 때마다(모델 바꿀 때도 새로) | 10분 안 쓰면 내림 |
| avatar-local 말하기(Wan2.2-S2V 약 60GB)·립싱크(SadTalker) | 실행마다(서브프로세스) | 실행 끝나면 반환 |
| tts-local(MeloTTS)·meeting-local(Whisper) | 처음 쓸 때 | 10분 안 쓰면 내림 → 다음에 다시 고름 |
| Ollama(gemma4:31b) | Ollama 스케줄러가 여유 VRAM 보고 배치 | `OLLAMA_KEEP_ALIVE` |

- 어느 GPU 에 올랐는지는 각 도구 로그(`server.log`·`studio.log`)와 진행 메시지에 `GPU 2 (여유 80GB) 에서 로드` 처럼 나옵니다.
- 환경변수: `GPU_POOL=2,3`(이 GPU 들만 후보로), `GPU_IDLE_UNLOAD_S=600`(안 쓰면 내리는 초, 0 이면 안 내림).
- 꼭 고정해야 할 때만(예외용): `tools.json` 의 그 도구에 `"gpus": "0"` 을 적으면 포털이 그 도구를 `CUDA_VISIBLE_DEVICES=<값>` 으로 띄워, 그 안에서만 고릅니다.

> portrait-local(LivePortrait)은 2026-10-06 포털에서 뺐습니다 — 얼굴 인식 모델(InsightFace)이 비상업 연구용 라이선스라 기술이전 등에 걸릴 수 있고 효용이 낮아서. 저장소 [gggg8657/portrait-local](https://github.com/gggg8657/portrait-local) 은 남겨 둡니다.

## 만든 사람

DongJu Kim (gggg8657) — 설계·구현
