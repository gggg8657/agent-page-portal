# agent-page-portal — 에이전트 페이지 포털

> **한 줄 요약** — `agent_page/` 아래 로컬 LLM 도구들을 한 화면에 모은 포털. 홈(검색·카테고리·인기 TOP·신규·노하우 피드)과
> 전체 목록(카드·정렬·필터·사용 통계·좋아요·즐겨찾기) 두 뷰, 도구별 시작/중지, README 보기. 파이썬 표준 라이브러리만.

```bash
python3 portal.py                 # http://localhost:8700
python3 portal.py start-all       # tools.json 의 설치된 도구 전부 기동 (각 폴더 setup.sh, PORT 전달)
python3 portal.py stop-all
bash audit.sh                     # 전체 점검: CDN 유출·포트 중복·LICENSE/NOTICE·selftest
```

- 도구 목록·메타(제목·설명·그룹·태그·부서·작성자·추가일·포트)는 `tools.json`. 새 도구는 한 줄 추가.
- 사용 통계(열기·좋아요)는 `_stats.json`(IP 기준 사용자 수). 즐겨찾기·최근 사용은 브라우저 localStorage.
- 노하우·우수사례 피드는 `feed.json`.
- 서버 배포 시 링크 호스트는 `PORTAL_HOST=<서버IP>` 로.
- 포털은 `agent_page/<도구>/` 폴더 구조를 전제로 하므로 각 도구 저장소를 같은 상위 폴더에 clone 한다.
