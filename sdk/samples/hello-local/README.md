# hello-local

개발자 업로드 샘플 도구입니다. 표준 라이브러리만 씁니다.

- 계정별 메모: `WORKSPACE/u/<사용자>/memo.json`
- LLM 한 줄 인사: `LLM_BASE_URL`(OpenAI 호환) · `LLM_MODEL`
- 포털 서명: `portal_auth.install(H, "hello-local")`

혼자 띄워 보기(포털 없이, 이 서버 안에서만 열림):

```bash
PORT=8810 python3 app.py      # http://127.0.0.1:8810/
python3 selftest.py
```

형식 규칙은 같이 들어 있는 `FORMAT.md` 를 보세요.
