# 그림 → 도형 API (`shapes-local`)
도식 그림(흐름도·구성도·조직도·칸 나눈 업무 흐름·슬라이드 캡처)을 이미지 입력 LLM(구조) + OpenCV(위치·모양·색) + OCR(한국어 글자)로 읽어 도식 문서(JSON)로 만들고, 화면에서 검토·수정한 뒤 PPTX(진짜 도형·연결선)·draw.io·SVG·Mermaid 로 내보냅니다. 그래프 그림이면 도형을 비워 두고 그래프 디지타이저를 권합니다. 명세: `openapi/shapes-local.json`.

포털 경로 접두어: `/t/shapes-local/` (예: `api/items` → `http://<서버>/t/shapes-local/api/items`)

> 결과는 추정입니다. 화면은 내보내기 전에 원본 위에 찾은 도형을 겹쳐 보여 주고 고칠 수 있게 하세요(`check: true` 는 강조). `api/meta` 의 `notice` 를 화면에 보여 주세요.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(VLM·OCR 연결 여부) | - | - |
| GET | `api/meta` | VLM·OCR 상태, 도형 종류, 내보내기 형식, 제한, 안내 문구, 그래프면 권할 도구 | - | - |
| GET | `api/items` | 변환한 그림 목록(최근 순) | - | 예 |
| POST | `api/analyze` | 그림 올려 변환 시작 `{b64, name, use_llm?, force?}` → `{id, status: "running"}` | OCR 1~2초 + VLM 10~40초 | 예 |
| GET | `api/items/{iid}` | 상태(`running`·`done`·`error`)·진행 글·경고·문서 `doc` | - | 예 |
| GET | `api/items/{iid}/image` | 올린 그림 PNG(문서 좌표 기준) | - | 예 |
| POST | `api/items/{iid}/doc` | 고친 문서 저장 `{doc}` | - | 예 |
| POST | `api/items/{iid}/reset` | 처음 변환 결과로 되돌리기 | - | 예 |
| POST | `api/items/{iid}/rerun` | 같은 그림 다시 변환 `{force?, use_llm?}`(고친 것은 사라짐) | VLM | 예 |
| POST | `api/items/{iid}/export` | 내보내기 `{format: pptx\|drawio\|svg\|mermaid\|json, doc?}` → 파일(attachment) | 1초 안팎 | 예 |
| POST | `api/items/{iid}/rename` | 이름 바꾸기 `{name}` | - | 예 |
| POST | `api/items/{iid}/delete` | 지우기(변환 중이면 409) | - | 예 |
| GET | `/` | 도구 자체 화면(`ui.html`) | - | - |

## 변환 (`api/analyze` → `api/items/{iid}` 를 1~2초마다)
- 입력: PNG·JPG·GIF·BMP·WEBP(맨 앞 바이트로 판별) base64(data URL 머리 있어도 됨), 15MB(`SHAPES_MB`), 짧은 변 32px 이상. EXIF 회전·투명 바탕(흰색) 처리, 긴 변 2400px 넘으면 줄임 — 좌표는 줄인 그림(`api/items/{iid}/image`) 기준.
- 단계(`progress`): 글자 읽는 중 → 도형 찾는 중 → AI 가 구조 읽는 중 → 맞추는 중.
  1. **OCR**: RapidOCR(onnxruntime CPU) + PaddleOCR 모델(검출 PP-OCRv6 small · 한국어 인식 PP-OCRv5 mobile). 화살촉을 글자로 읽은 `→`·한 글자 영문·숫자 조각은 버림.
  2. **영상처리**: 테두리(Canny + 색 차이)로 나뉜 닫힌 칸 = 도형 후보. 채움 비율·옆 테두리 모양·모서리로 rect/round/diamond/ellipse/hexagon/parallelogram, 칸 안 색 = `fill`, 바깥 고리의 어두운 색 = `stroke`.
  3. **VLM**(이미지 입력 LLM, 긴 변 1600px 로 보냄): `kind`(diagram·chart·photo·other)·노드(0~1000 정규화 좌표)·연결선(from·to·label·dashed·arrow)·묶음(label·children) — JSON 스키마 안내 디코딩, 안 되는 서버면 JSON 객체.
  4. **합치기**: VLM 노드 ↔ 칸을 겹침(IoU) 큰 쌍부터, 못 맞춘 것은 글자가 비슷한 칸으로. 원통은 뚜껑·몸통 칸을 합침. 글자는 OCR(띄어쓰기·줄바꿈·글머리표만 VLM 에서, OCR 이 I·l·1 같은 가는 글자만 빠뜨렸으면 VLM 글). 글 상자·묶음 이름·연결선 이름도 OCR 줄에 맞춤. 묶음 변은 근처의 가장 진한 가로·세로 선으로(점선도).
  5. **연결선 확인**: 도형 밖 잉크에서 선 조각(점선은 가로·세로로 이어 붙임)·화살촉(열림 연산 뒤 남는 굵은 덩어리)을 찾아, 두 도형만 잇는 선이면 방향·양쪽 화살표·점선을 고침. VLM 이 놓친 두 도형 사이 선은 `check: true` 로 추가. 여러 도형으로 갈라지는 줄기는 방향을 바꾸지 않음.
- `kind` 가 `chart`·`photo` 면 `nodes`·`edges`·`groups` 를 비워 둠 — `rerun {force: true}` 로 그래도 도형으로.
- VLM 이 없거나 실패하면 영상처리·OCR 만(`warnings` 에 적음, `sources.llm: false`): 글자가 든 칸 = 노드, 다른 칸을 2개 이상 품은 칸 = 묶음, 두 도형을 잇는 선 = 연결선.
- 시각은 UTC ISO(`+00:00`).

## 문서 (`doc`)
- `nodes[]`: `{id, shape: rect|round|diamond|ellipse|cylinder|parallelogram|hexagon|image|text, text(줄바꿈 \n), bbox: [x1,y1,x2,y2], fill, stroke, color(글자), font_px, check, src: {text: ocr|llm|none}, dashed?, bold?, rotation?(90|-90, 세로 글), note?(확인할 까닭)}` — `image` 는 아이콘·캡처 등 도형으로 바꾸기 어려운 부분(내보낼 때 원본에서 bbox 만큼 잘라 그림으로)
- 연결선 `from`·`to` 는 묶음 id 도 됨(판에 닿는 화살표). `color`·`label_color`·`note` 선택. 묶음은 `fill`·`stroke`·`label_box`(판 바깥 위 제목)·`label_color` 선택
- 긴 변 1100px 미만 그림은 안에서 2~2.5배 키워 처리(`sources.upscale`), 좌표는 원본 크기
- `edges[]`: `{id, from, to, label, dashed, arrow: end|both|none, check, label_box?}`
- `groups[]`: `{id, label, bbox, children(노드·묶음 id), dashed, fill, stroke, check}`
- 저장(`doc`·`export` 의 `doc`) 때 서버가 검사: id 형식(`[a-z]\w{0,15}`, 겹치면 400), 좌표는 그림 안으로, 없는 노드를 가리키는 선·자기 자신으로 가는 선은 버림, 노드 800·선 1,500·묶음 200개까지, 문서 4MB.

## 내보내기 (`api/items/{iid}/export`)
- **pptx**: 슬라이드 가로 13.333in, 세로는 그림 비율. 도형 = 미리 정의된 도형(사각형·둥근 사각형·마름모·타원·원통 can·평행사변형·육각형), 글 상자, 묶음 = 뒤에 깐 틀(이름은 왼쪽 위 굵게). 연결선 = `p:cxnSp` 직선·꺾은선(세로 꺾은선은 90° 돌린 bentConnector3), **도형 연결점에 붙임**(`stCxn`/`endCxn`; 연결점 번호가 확실한 도형만 — 평행사변형·육각형 위아래는 좌표만), 화살표(`tailEnd`/`headEnd` triangle)·점선. 글꼴 맑은 고딕, 크기는 글자 높이에서. 2쪽 = 원본 그림.
- **drawio**: mxGraph XML. 묶음은 `container=1` 이고 안 도형은 묶음 기준 상대 좌표, 연결선은 `source`/`target` + `orthogonalEdgeStyle`.
- **svg**: 도형·글·화살표(marker)·점선.
- **mermaid**: `flowchart TD|LR`(연결선 방향으로), 묶음 = subgraph, 채움 색 = style. 도형 2개·연결선 1개 이상이 아니면 400.
- **json**: 문서 그대로.
- 파일 이름 = 올린 그림 이름(확장자 뺀 것) + 형식 확장자, `Content-Disposition: attachment; filename*=UTF-8''…`.

## 제한·오류
- 사용자당 변환 동시 1(409 `busy`)·1시간 30번(429 `rate_limited`, `Retry-After`, `SHAPES_RATE_N`), 서버 동시 2(`SHAPES_JOBS_MAX`, 넘으면 '차례를 기다리는 중'), VLM 호출 시간 제한 240초.
- 사용자당 60개 보관(`SHAPES_ITEMS_MAX`, 넘으면 오래된 것부터 지움). 서버가 변환 중에 다시 시작되면 그 항목은 `error`.
- 입력 오류 400 `bad_request`(그림이 아님·너무 큼·너무 작음·b64 아님·변환이 안 끝난 항목 저장/내보내기 …), 본문이 너무 크면 413, 없는 항목 404.
- 저장: `WORKSPACE/u/<사용자>/items/<iid>/`(image.png·doc.json·doc.orig.json·meta.json). 그림은 포털 안 LLM 서버로만 보냄.
