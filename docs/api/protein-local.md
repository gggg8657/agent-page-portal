# 단백질 구조 예측 API (`protein-local`)
서열 → 3D 구조(Boltz-2/Boltz-1), 단백질 × 리간드 결합 친화도 순위(Boltz-2), 구조 → 서열 설계(ProteinMPNN)를 GPU 작업 큐로 돌리는 도구.
포털 경로 접두어: `/t/protein-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/meta` → `<서버>/t/protein-local/api/meta`)

- 요청 본문은 전부 **JSON** (`Content-Type: application/json`). multipart 업로드는 없음 — 구조 파일도 텍스트로 JSON 안에 넣음.
- 응답은 기본 JSON(`Cache-Control: no-store`). 오류는 `{"error": "..."}` + 상태 코드(400 입력 오류, 404 없는 작업·경로, 500 서버 예외 `"<예외이름>: <메시지>"`).
- 스트리밍(SSE)은 없음. 긴 작업은 **작업 id → 폴링** 방식.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 `{"ok": true}` | - | - |
| GET | `api/meta` | 엔진·모드·한도·예시·GPU 상태·큐 길이 | nvidia-smi 호출(가벼움) | - |
| GET | `api/models` | LLM 모델 이름 목록(해설용) | - | - |
| GET | `api/jobs` | 내 작업 목록(최근 200개) | - | 예 |
| GET | `api/job/<id>` | 작업 한 개 상태 | - | 예 |
| GET | `api/job/<id>/log?since=N` | 진행 로그(증분) | - | 예 |
| GET | `api/job/<id>/result` | 결과 JSON(완료 후) | - | 예 |
| GET | `api/job/<id>/validations` | 설계 작업에서 파생된 재예측 작업들 | - | 예 |
| GET | `api/job/<id>/fasta` | 설계 서열 FASTA 다운로드 | - | 예 |
| GET | `api/job/<id>/input` | 설계 입력 구조 PDB | - | 예 |
| GET | `api/job/<id>/file/<name>` | 모델 PDB/mmCIF, 토큰·PAE JSON | - | 예 |
| GET | `api/job/<id>/zip` | 작업 전체 ZIP | - | 예 |
| POST | `api/validate` | 구조 예측 입력 검사(토큰 수 등) | - (가벼움) | - |
| POST | `api/validate_affinity` | 친화도 입력 검사 | - | - |
| POST | `api/pdbinfo` | PDB/mmCIF 텍스트 → 사슬 정보 | mmCIF 면 변환 프로세스(수 초) | - |
| POST | `api/submit` | 작업 등록(구조·친화도·설계) | **GPU, 수십 초~수 분** | 예 |
| POST | `api/job/<id>/cancel` | 대기/실행 작업 취소 | - | 예 |
| POST | `api/job/<id>/delete` | 작업·파일 삭제 | - | 예 |
| POST | `api/job/<id>/explain` | LLM 결과 해설 | **LLM, 수십 초** | 예 |
| POST | `api/job/<id>/validate` | 설계 서열들을 구조 예측 작업으로 재예측 | **GPU (작업 여러 개 등록)** | 예 |

그 밖에 `GET /`(화면 `ui.html`), `GET static/<파일>`(.js/.css/.txt 만, 예: `static/3Dmol-min.js`).

작업 id 형식: `YYYYMMDD-HHMMSS-xxxx`(16진 4자리). 형식이 다르면 404.

## 엔드포인트

### GET api/meta
- 응답 예:
```json
{"engines": [{"id": "boltz2", "label": "Boltz-2", "ligand": true, "affinity": true},
             {"id": "boltz1", "label": "Boltz-1", "ligand": true, "affinity": false}],
 "mpnn": true, "max_aff_ligands": 20, "max_designs": 64,
 "modes": [{"id": "fast", "label": "빠름", "recycling": 1, "steps": 50},
           {"id": "standard", "label": "표준", "recycling": 3, "steps": 200},
           {"id": "precise", "label": "정밀", "recycling": 10, "steps": 200}],
 "max_res": 1500, "max_chains": 10, "max_samples": 5, "timeout": 3600,
 "examples": [{"name": "Trp-cage (20aa)", "note": "...", "fasta": ">TrpCage_TC5b\nNLYIQWLKDGGPSSGRPPPS"}],
 "gpus": [{"index": "0", "name": "NVIDIA H100 NVL", "used": 1267, "free": 94064, "total": 95830}],
 "model": "<LLM 모델>", "queue": 0, "running_n": 0, "running": []}
```
- `engines`: 실제로 돌릴 수 있는 엔진만(가중치가 있어야 나옴). `affinity` 가 true 인 엔진에서만 친화도 가능.
- `mpnn`: ProteinMPNN(서열 설계) 사용 가능 여부.
- `max_res`: 토큰 한도(잔기 수 + 리간드 무거운 원자 수). `timeout`: 작업 하나 최대 초.
- `queue`: 전체 대기 작업 수(모든 사용자 공통 GPU 줄). `running_n`: 지금 실행 중인 작업 수(모든 사용자). `running`: 그중 **내** 작업 id 목록 — 다른 사용자의 작업 id 는 주지 않음. LLM 서버 주소는 주지 않음(모델 이름 `model` 만).
- 화면은 15초마다 다시 불러 GPU 줄을 갱신함.

### GET api/models
- 응답: `["qwen3:8b", ...]` — LLM 서버에서 받은 모델 이름. 연결 실패 시 `[]`.

### GET api/jobs
- 응답: 배열(최신 순, 최대 200개). 항목:
```json
{"id": "20261007-064038-4edd", "kind": "fold", "title": "TrpCage_TC5b", "status": "done",
 "created": "2026-10-07T06:40:38", "engine": "boltz2", "mode": "fast", "tokens": 20,
 "error": null, "elapsed": 22.0, "ref": null,
 "plddt": 95.5, "ptm": 0.492, "iptm": null, "rmsd": null, "best": "평균 pLDDT 95.5",
 "n_chains": 1, "n_ligands": 0, "position": 0}
```
- `kind`: `fold`(구조) · `affinity`(친화도) · `design`(서열 설계).
- `status`: `queued` · `running` · `done` · `error` · `canceled`.
- `position`: 대기 순번(1 = 다음 차례), 대기 중이 아니면 0.
- `ref`: 설계 재예측 작업이면 `{"job": <설계 작업 id>, "design": <설계 번호, 0=원서열>}`.
- 화면은 6초마다 다시 불러 목록을 갱신.

### GET api/job/&lt;id&gt;
- 응답: 작업 저장본(`job.json`) 전체 + `position`. 공통 필드: `id, kind, title, status, created, started, finished, elapsed, error, tokens, chains, ligands, gpu, gpu_free_mb, summary(완료 시)`.
  - fold: `engine, mode, samples, affinity, msa:"none", ref?`, `chains[]={id,name,seq}`, `ligands[]={id,label,name,atoms,smiles|ccd}`
  - affinity: 위와 같고 `ligands[]` 에 `file`(`lig_01`…) 추가
  - design: `engine:"proteinmpnn", model, n, temperature, design_chains, fixed, omit, source`, `chains[]={id,n,seq}`
- 오류: 없는 id·남의 작업 → 404 `{"error":"없음"}`.

### GET api/job/&lt;id&gt;/log?since=N
- 응답: `{"lines": ["[app] GPU 0 (...) 여유 ... MiB 에서 실행", "..."], "next": 12}`
- 다음 호출에 `since=next` 를 넘기면 새 줄만 받음. 로그 파일이 아직 없으면 `{"lines": [], "next": 0}`.

### GET api/job/&lt;id&gt;/result
- 완료(`status:"done"`) 후에만. 그 전이면 404. LLM 해설을 만든 적 있으면 `explain`(마크다운 문자열)이 붙음.
- **fold**:
```json
{"summary": {"plddt": 95.5, "ptm": 0.49, "iptm": null, "confidence": 0.9,
             "gpu_peak_mb": 3100, "gpu_name": "...", "engine_seconds": 15.2,
             "affinity": null, "best": "평균 pLDDT 95.5", "rmsd": 1.2, "ref_n": 20, "ref_note": "..."},
 "samples": [{"k": 0, "plddt": 95.5, "ptm": 0.49, "iptm": null, "confidence": 0.9,
              "ligand_iptm": null, "complex_pde": 0.4, "pae_mean": 2.1,
              "chains": [{"id": "A", "plddt": 95.5, "n": 20, "ptm": 0.49}],
              "pairs": [{"a": "A", "b": "B", "iptm": 0.8, "pae": 4.2}],
              "bands": {"vh": 90.0, "h": 10.0, "l": 0.0, "vl": 0.0},
              "low_segments": [{"chain": "A", "start": 1, "end": 5, "len": 5, "mean": 42.1}]}]}
```
  - `samples[0]` 이 최상위(신뢰도 순). `bands` 는 pLDDT ≥90 / 70–90 / 50–70 / <50 비율(%).
  - `summary.affinity`(리간드 1개 + `affinity:true` 일 때): `{"log10_ic50_uM", "ic50_uM", "pic50", "p_binder", "models": [..]}`.
  - `rmsd·ref_n·ref_note` 는 재예측 검증 작업(`ref` 있음)에서만.
- **affinity**:
```json
{"summary": {"n": 3, "n_ok": 3, "top": "아스피린", "top_p": 0.71, "best": "1위 ...", "gpu_peak_mb": 0, "gpu_name": "", "engine_seconds": 0},
 "ligands": [{"i": 0, "file": "lig_01", "name": "아스피린", "label": "CC(=O)...", "smiles": "...", "ccd": null,
              "log10_ic50_uM": 1.2, "ic50_uM": 15.8, "pic50": 4.8, "p_binder": 0.71, "models": [..],
              "plddt": 88.0, "iptm": 0.8, "ligand_iptm": 0.7, "confidence": 0.8, "lig_plddt": 80.0,
              "rank_p": 1, "rank_ic50": 2, "sample": {/* fold 의 samples 항목과 같은 모양 */}},
             {"i": 1, "name": "...", "error": "예측 실패 (로그 확인)"}]}
```
  - 실패한 리간드는 `error` 만 있음(나머지 리간드는 계속 진행).
- **design**:
```json
{"summary": {"n": 8, "best_score": 0.95, "mean_recovery": 0.41, "best": "설계 8개, 최저 score 0.95", "gpu_peak_mb": 0, "gpu_name": "", "engine_seconds": 0},
 "native": "MQIF...:GIVE...", "chain_ids": ["A", "B"],
 "designs": [{"i": 3, "seq": "MKIE...:GLVE...", "confidence": 0.38, "score": 0.95, "global_score": 1.10,
              "recovery": 0.42, "mutations": 40}]}
```
  - 여러 사슬 서열은 `:` 로 이어 붙임(`chain_ids` 순서). `designs` 는 `score` 오름차순.

### GET api/job/&lt;id&gt;/validations
- 설계 작업 id 를 주면 그 작업을 `ref` 로 가진 재예측 작업 목록:
  `[{"id": "...", "design": 3, "status": "done", "plddt": 88.1, "ptm": 0.7, "rmsd": 1.4, "error": null}]` (`design: 0` = 원서열)

### GET api/job/&lt;id&gt;/file/&lt;name&gt;
- 허용 이름(정규식): `(l\d{2}_)?(model|pae|tokens)_\d.(pdb|cif|json)` 또는 `ref_aligned.pdb`. 그 밖은 404.
  - `model_K.pdb` / `model_K.cif` — 샘플 K 의 구조(Content-Type `chemical/x-pdb` / `chemical/x-mmcif`). B 인자 = pLDDT.
  - `tokens_K.json` — 토큰 배열: 단백질 `{"chain","resi","resn","plddt","het":false}`, 리간드 원자 `{"chain","resi","resn","atom","plddt","het":true}`.
  - `pae_K.json` — `{"n": 토큰수, "factor": 풀링배수, "grid": [[...]], "bounds": [사슬 경계 인덱스], "max": 31.75}`. 500×500 넘으면 평균 풀링됨. 없을 수 있음(404).
  - 친화도 작업은 리간드 i 마다 접두어 `lNN_` (예: `l00_model_0.pdb`, `l00_tokens_0.json`). 샘플은 0 하나뿐.
  - `ref_aligned.pdb` — 재예측 작업에서 원구조를 예측 좌표계로 겹친 것.
- `?dl=1` 이면 `Content-Disposition: attachment; filename="<id>_<name>"` (다운로드). 없으면 인라인.

### GET api/job/&lt;id&gt;/fasta · input · zip
- `fasta`: 설계 작업의 `designs.fa` (`text/plain; charset=utf-8`, 파일명 `designs_<id>.fa`). 첫 레코드 `>native ...`, 다음 `>design_<i> score=.. global_score=.. confidence=.. seq_recovery=..`.
- `input`: 설계 입력 구조 PDB (`chemical/x-pdb`, 인라인).
- `zip`: `application/zip`, 파일명 `protein_<id>.zip` — job.json, result.json, 입력, 로그, 해설, models/(pdb·cif·pae), raw/(npz·affinity json), inputs/(친화도 yaml).
- 해당 파일이 없으면 404.

### POST api/validate
- 구조 예측 입력 검사(작업은 만들지 않음). 화면은 입력이 바뀔 때마다 0.35초 디바운스로 호출.
- 요청:

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| fasta | string | 예 | FASTA(레코드 여러 개 = 복합체, 사슬 A,B,…) 또는 맨 서열. 표준 아미노산 20자, 사슬당 4잔기 이상 |
| ligands | string | 아니오 | 한 줄에 하나: CCD 코드(`ATP`) 또는 SMILES. `ccd:`/`smiles:` 접두어로 강제. 뒤에 공백 + 이름 가능 |
| engine | string | 아니오 | `boltz2`(기본) / `boltz1` |
| affinity | bool | 아니오 | 결합 친화도(boltz2, 리간드 정확히 1개일 때만) |
| samples | int | 아니오 | 1–5 (기본 1) |
| mode | string | 아니오 | `fast` / `standard`(기본) / `precise` |
| seed | 숫자 문자열 | 아니오 | 재현용 시드 |

- 응답: `{"errors": ["..."], "tokens": 96, "chains": [{"id":"A","name":"Ubiquitin","len":76}], "ligands": [{"id":"B","label":"ATP","name":"ATP","atoms":40,"ccd":"ATP"}]}`
  - **오류가 있어도 200** 이고 `errors` 배열에 담김(빈 배열이면 통과).

### POST api/validate_affinity
- 요청: `fasta`(단백질, 필수), `ligands`(필수, 한 줄에 하나, 최대 20), `mode`, `seed`. 엔진은 boltz2 고정.
- 응답: `api/validate` 와 같은 모양. `tokens` = 단백질 잔기 + 가장 큰 리간드 원자 수. 리간드마다 `file`(`lig_01`…).

### POST api/pdbinfo
- 요청: `{"pdb": "<PDB 또는 mmCIF 텍스트>"}`
- 응답: `{"chains": [{"id": "A", "n": 76, "first": "1", "last": "76"}]}` (`first/last` 는 잔기 번호 문자열, 삽입코드 포함 가능)
- 오류: mmCIF 변환 실패 400. CA 원자가 없거나 빈 입력이면 오류 없이 `{"chains": []}`.

### POST api/submit
- 작업을 큐에 넣고 바로 작업 객체(`api/job/<id>` 와 같은 모양, `status:"queued"`)를 돌려줌. 결과는 폴링.
- 공통: `kind`(`fold` 기본 / `affinity` / `design`), `title`(선택, 80자, 없으면 자동), `seed`(선택).
- **fold**: `api/validate` 의 필드 전부 + 선택 `ref: {"job": <설계 작업 id>, "design": <번호>}`(원구조와 RMSD 비교).
- **affinity**: `api/validate_affinity` 의 필드.
- **design**:

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| from_job | string | 둘 중 하나 | 이 도구의 구조 예측 작업 id (그 작업의 `model_<k>.pdb` 사용) |
| k | int | 아니오 | from_job 의 샘플 번호(기본 0) |
| pdb | string | 둘 중 하나 | PDB 또는 mmCIF 텍스트(8MB 이하, 잔기 3000 이하). 리간드·물 무시 |
| filename | string | 아니오 | 표시용 원본 파일 이름 |
| chains | string[] | 아니오 | 설계할 사슬(기본 전부) |
| fixed | string | 아니오 | 고정 잔기 `A1-10, A25, B`(B = 사슬 전체) |
| n | int | 아니오 | 서열 수 1–64(기본 8, 16 단위 배치로 올림될 수 있음) |
| temperature | number | 아니오 | 0.01–1.5 (기본 0.1) |
| model | string | 아니오 | `protein_mpnn`(기본) / `soluble_mpnn` |
| omit | string | 아니오 | 제외할 아미노산 글자(예: `C`) |

- 오류: 검사 실패 → 400 `{"error": "줄바꿈으로 이은 오류들"}`.
- 메모: GPU 1장에 작업 1개씩, **모든 사용자가 한 줄**로 대기. 작은 단백질 30초 안팎, 수백 잔기 1–3분, 친화도는 리간드당 30초 안팎, 최대 `timeout`(기본 3600초). 서버 재시작 시 실행 중 작업은 `error`, 대기 작업은 다시 줄 섬.

### POST api/job/&lt;id&gt;/cancel
- 본문 `{}`. 대기 중이면 즉시 `canceled`, 실행 중이면 프로세스 종료 후 `canceled`. 응답: 작업 객체(실행 중 취소면 반영 전 상태일 수 있으니 다시 폴링). 내 작업이 아니거나 없는 id → 404.

### POST api/job/&lt;id&gt;/delete
- 본문 `{}`. 응답 `{"ok": true}`. 실행 중이면 400 `{"error":"실행 중인 작업은 먼저 취소하세요"}`. 내 작업이 아니거나 없는 id → 404.

### POST api/job/&lt;id&gt;/explain
- 요청: `{"k": 0, "model": "<선택: LLM 모델 이름>"}` (`k` = 해설할 샘플, fold 만 의미 있음)
- 응답: `{"text": "<한국어 마크다운>"}` — 작업 폴더에 저장돼 이후 `result.explain` 으로도 나옴.
- 동기 호출(스트리밍 없음), 수십 초. LLM 연결 실패 → 500. 완료 전 작업 → 404.

### POST api/job/&lt;id&gt;/validate
- 설계 작업의 서열을 구조 예측(fold) 작업으로 다시 등록해 원구조와 RMSD 비교.
- 요청: `{"designs": [3, 5], "native": true, "mode": "standard"}` (`designs` 최대 20개, `native:true` 면 원서열(번호 0)도 추가)
- 응답: 새로 만든 fold 작업 객체 배열. 진행은 `api/job/<설계id>/validations` 로 확인.
- 오류: 고른 것이 없으면 400. 고른 서열 중 하나라도 검사에 걸리면 아무것도 등록하지 않고 400(`설계 N: …`). 등록 도중 실패하면 이미 넣은 작업을 대기열·폴더에서 지운 뒤 오류를 돌려줌(반쯤 등록된 작업이 남지 않음).

## 흐름 예

### 1) 구조 예측 → 결과 보기
```js
const P = '/t/protein-local/';
const api = async (p, body) => {
  const r = await fetch(P + p, body === undefined ? {} : {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const j = await r.json(); if (!r.ok) throw new Error(j.error); return j;
};
const form = {fasta: '>Ubiquitin\nMQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG',
              engine: 'boltz2', mode: 'fast', samples: 1};
const v = await api('api/validate', form);          // 실행 전 검사
if (v.errors.length) throw new Error(v.errors.join('\n'));
const job = await api('api/submit', form);          // 큐에 넣기
let since = 0, j;
do {                                                // 1.5초 폴링 (화면과 같음)
  await new Promise(r => setTimeout(r, 1500));
  j = await api('api/job/' + job.id);
  const lg = await api(`api/job/${job.id}/log?since=${since}`); since = lg.next;
  console.log(lg.lines.join('\n'));
} while (j.status === 'queued' || j.status === 'running');
if (j.status === 'done') {
  const res = await api(`api/job/${job.id}/result`);
  const pdb = await fetch(P + `api/job/${job.id}/file/model_0.pdb`).then(r => r.text());
  const pae = await api(`api/job/${job.id}/file/pae_0.json`).catch(() => null);
}
```

### 2) 업로드한 PDB 로 서열 설계 → 재예측 검증
```js
const text = await file.text();                                   // <input type=file> 의 PDB
const {chains} = await api('api/pdbinfo', {pdb: text});           // 사슬 고르기용
const d = await api('api/submit', {kind: 'design', pdb: text, filename: file.name,
  chains: chains.map(c => c.id), fixed: 'A1-10', n: 8, temperature: 0.1});
// ... 위와 같이 폴링 → api(`api/job/${d.id}/result`).designs
await api(`api/job/${d.id}/validate`, {designs: [1, 2], native: true, mode: 'standard'});
const vals = await api(`api/job/${d.id}/validations`);            // rmsd 확인 (주기적으로)
```

## 화면만의 것 / 주의
- **3D 뷰어**는 화면이 `static/3Dmol-min.js` 로 PDB 텍스트를 직접 그림. pLDDT 색(≥90/70/50 경계), 사슬 색, PAE 히트맵(canvas)과 PNG 저장, pLDDT 그래프는 전부 화면 계산 — API 는 PDB·`tokens_K.json`·`pae_K.json` 만 줌.
- 작업 링크: 화면은 주소 끝 `#<작업id>` 로 작업을 엶. 이 도구 자체에는 공유 기능이 없음(작업은 사용자별 폴더라 다른 계정은 404).
- 입력 폼 내용은 화면이 브라우저 `localStorage`(`protein-form`)에 저장 — 서버에 저장되지 않음.
- 설계 탭에서 "예측 결과에서 고르기"는 화면이 `api/jobs` 를 걸러(완료된 fold) 목록을 만들고, `api/job/<id>` 의 `chains` 로 사슬을 보여 줌.
- **사용자별 데이터**: 포털이 넣는 `X-Agent-User` 헤더로 작업 폴더가 나뉨. 남의 작업 id 는 404. 서버 주인(owner)은 예전 공용 폴더를 봄. GPU 큐는 모든 사용자 공통이고 `api/meta` 는 전체 개수(`queue`·`running_n`)와 내 실행 중 작업(`running`)만 줌. 작업 로그·오류 문구 속 서버 폴더 경로는 지우고 파일 이름만 남김.
- 권한 구분(owner 만 되는 API)은 없음.
- 모든 POST 본문은 JSON 이며 크기 제한은 서버에 따로 없음(설계 구조만 8MB 검사).
