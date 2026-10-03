# agent-page-portal — 에이전트 페이지 포털

> **한 줄 요약** — `agent_page/` 아래 로컬 LLM 도구들을 한 화면에 모은 포털. 홈(검색·카테고리·인기 TOP·신규·노하우 피드)과
> 전체 목록(카드·정렬·필터·사용 통계·좋아요·즐겨찾기) 두 뷰, 도구별 시작/중지, README 보기. 파이썬 표준 라이브러리만.

```bash
python3 portal.py                 # http://localhost:8700
python3 portal.py start-all       # tools.json 의 설치된 도구 전부 기동 (각 폴더 setup.sh, PORT 전달)
python3 portal.py stop-all
bash audit.sh                     # 전체 점검: CDN 유출·포트 중복·LICENSE/NOTICE·selftest
```

- 도구 목록·메타는 `tools.json`(요청마다 다시 읽음). "+ 내 에이전트 만들기"로도 추가되며 `"hidden": true` 면 목록에서 숨김(프록시는 됨).
- **DB**: `AGENT_DATA/portal.db` sqlite 파일 하나(WAL). 사용 통계(열기·좋아요)·피드 글·피드 좋아요·사용자(IP 또는 `X-Forwarded-User` 헤더) 랭킹. 즐겨찾기·최근 사용만 브라우저 localStorage.
- **데이터 한곳에**: `AGENT_DATA`(기본 `../_data`) 아래에 `portal.db` 와 각 도구의 작업 폴더(`<도구>/…`)가 모입니다. 포털이 도구를 띄울 때 `WORKSPACE=$AGENT_DATA/<도구>` 를 넘기고, 모든 도구가 그 변수를 읽습니다. 백업은 이 폴더 하나.
- **단일 진입점**: 모든 도구는 `http://<포털>:8700/t/<도구폴더>/` 로 프록시된다(SSE·파일 다운로드 포함). 사용자는 포트 하나만 열면 되고, 도구 UI 는 상대경로만 쓴다. 직접 포트 주소는 `url_direct`(`PORTAL_HOST=<서버IP>`).
- 포털은 `agent_page/<도구>/` 폴더 구조를 전제로 하므로 각 도구 저장소를 같은 상위 폴더에 clone 한다.
