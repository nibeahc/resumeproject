# ROADMAP.md — 서비스 빌드 계획서

> **갱신일**: 2026-09-21
> **목표**: 자소서 작성기 → **지원 파이프라인 서비스** 완성
> **이 문서**: 무엇을 만드는가 (사양)
> **`PROMPTS.md`**: 어떻게 시작하는가 (CLI 에 붙여넣는 명령)
> **`PROGRESS.md`**: 어디까지 했는가 (에이전트가 매 세션 갱신)

읽는 순서: `Claude.md` → `PROGRESS.md` → 이 문서의 해당 Stage → `PROMPTS.md`

---

## 0. 완성 정의 (Definition of Done)

아래가 **터미널 없이 브라우저만으로** 가능하면 서비스 완성이다.

```text
1. 로그인한다
2. 원본 서류를 올리거나, 없으면 대화로 경험을 발굴한다
3. 경험 DB 가 만들어지고, 내가 직접 고칠 수 있다
4. 공고를 등록하거나 채용 API 로 탐색한다
5. 내 경험과 공고 조건이 비교되고, 적합 공고와 도전 공고가 추천된다
6. 추천 이유, 연결되는 경험, 부족한 조건이 보인다
7. 쓸 경험을 내가 고른다
8. 이력서, 자소서, 포트폴리오 초안이 공고에 맞춰 생성된다
9. 고치고, 버전이 남고, DOCX 나 PDF 로 받는다
10. 관심 → 준비중 → 지원완료 → 결과 로 상태를 관리한다
```

---

## 1. 아키텍처 원칙 (모든 Stage 공통)

### 1-A. 데이터 두 층으로 분리한다

**Markdown 유지** — 사람이 읽고 고치는 자산

```text
users/[이름]/experiences/*.md      경험 DB
users/[이름]/profile.md            인적사항, 가치관
users/[이름]/writing-style.md      자동 분석 어투
users/[이름]/writing-rules.md      사용자가 정한 규칙
users/[이름]/index.md              경험 색인
users/[이름]/관심직무.md            희망 직무
outputs/[이름]/**                  생성 서류 본문
jobs/*.md                          공고 원문 캐시
```

git 으로 이력이 남고, 사용자가 파일째 들고 나갈 수 있고, 에디터에서 직접 고칠 수 있다.
**이 성질을 버리지 않는다.**

**SQLite 신규** (`data/app.db`) — 기계가 조회, 정렬, 중복판정하는 대상

```sql
jobs(id, source, source_job_id, company, position, deadline,
     employment_type, required_json, preferred_json,
     url, raw_path, content_hash, fetched_at, closed)

matches(id, user, job_id, score, required_hit, preferred_hit,
        evidence_json, missing_json, created_at)

applications(id, user, job_id, status, updated_at)
application_events(id, application_id, from_status, to_status, note, created_at)

documents(id, user, job_id, kind, version, path, char_count, created_at)
-- kind: coverletter | resume | portfolio
```

**경계를 넘지 않는다.** 경험 DB 를 SQLite 로 옮기지 말고, 공고 목록을 md 로 관리하지 말 것.

### 1-B. 스킬 파일은 한 벌만 유지한다

`skills/*.md` 는 CLI 와 웹이 **같은 파일을 공유**한다.
웹용으로 복제하거나 코드에 프롬프트를 하드코딩하지 않는다.
웹은 이 파일을 읽어서 프롬프트 템플릿으로 쓴다.

### 1-C. 환각 방지

- 경험을 지어내거나 과장하지 않는다 (기존 `Claude.md` 규칙 유지)
- 매칭 판정에 **근거 경험 파일명을 반드시 붙인다.** 근거 없는 "충족" 판정 금지
- 사용자가 말하지 않은 수치를 채워 넣지 않는다. 빈 항목은 `⚠️` 태그로 남긴다

### 1-D. 외부 자료 수집은 단계별 정책을 따른다

정책 원본은 `Claude.md` 의 **"외부 자료 수집 정책"** 절이다. 여기에 복제하지 않는다.
요지만: 기본 허용은 사용자 붙여넣기와 공식 API, 조건부 허용은 접근 정책을 확인한 공개 URL
단건 수집(출처·시점 기록), 금지는 로그인·캡차 우회와 대량 수집, 실패 시에는 추론 대신
사용자에게 원문을 요청한다.

---

## 2. 빌드 순서

| Stage | 내용 | 규모 | 왜 이 순서인가 |
|---|---|---|---|
| **S0** | 기반 정리, 데이터 계층 | 1일 | 이후 전부가 여기 의존 |
| **S1** | 생성 엔진 웹 이관 | 1주 | **단일 병목.** 여기 전에는 서비스가 아니다 |
| **S2** | 텍스트 추출 코드화 | 3일 | 온보딩 실패율의 원인 |
| **S3** | 경험 선택 분리 + 대화형 발굴 | 1주 | 차별점이 만들어지는 지점 |
| **S4** | 공고 등록과 파싱 | 3일 | 매칭의 입력 |
| **S5** | 매칭 엔진 | 1주 | 이미지 요구사항의 핵심 |
| **S6** | 채용 API 연동 | 4일 | 커버리지 확대 |
| **S7** | 서류 3종 | 1주 | 이력서, 포트폴리오 추가 |
| **S8** | 버전과 내보내기 | 4일 | |
| **S9** | 지원 상태 관리 | 4일 | 재방문 이유를 만든다 |
| **S11** | 생성 신뢰성 (설계검토 반영) | 1주 | **S10 보다 먼저.** 생성 결과를 믿을 수 없는 상태로 사용자를 늘리면 안 된다 |
| **S10** | 인증과 배포 | 1주 | 마지막. 먼저 하면 나머지가 느려진다 |

**S1 이 왜 먼저인가**: 현재 `web/app.py` 208줄에 LLM 호출이 한 줄도 없다.
전부 파일 CRUD 다. 사용자는 웹에서 업로드하고, 터미널로 가서 명령하고, 다시 웹으로 돌아와야 한다.
터미널을 쓸 줄 아는 사람만 쓸 수 있고, 그 사람에게는 웹이 필요 없다.

---

## 3. Stage 상세

### S0. 기반 정리와 데이터 계층

#### S0-1. 저장소 자동 점검 — **[완료 2026-09-21]**
`scripts/repo-health.sh` + `.githooks/` 설치, `core.hooksPath=.githooks`.
index.lock, 중단된 tmp_pack, fsck, iCloud evict, 개인정보 추적, 삭제된 추적파일 6종을 점검하고 자동 복구한다.
pre-commit 에서 `--check` 모드로 돌아 문제가 있으면 커밋을 막는다.

#### S0-2. Flask 보안 3종
- **대상**: `web/app.py`
- `debug=True` → `False`
- `/upload/<name>` 의 `f.filename` 에 `werkzeug.utils.secure_filename()` 적용
- 경로 검증 헬퍼를 `/output`, `/experience` 및 각 `/save` 에 적용
  ```python
  def safe_path(base: Path, *parts) -> Path:
      p = base.joinpath(*parts).resolve()
      if not p.is_relative_to(base.resolve()):
          abort(400)
      return p
  ```
- **완료 조건**: `curl -s -o /dev/null -w '%{http_code}' 'http://localhost:8080/output/채빈/..%2f..%2fClaude.md'` 가 `400`

#### S0-3. SQLite 계층
- **대상**: `lib/db.py`, `lib/schema.sql` (신규)
- 1-A 의 5개 테이블 생성, 마이그레이션 함수 `init_db()`
- `data/app.db` 는 gitignore 됨 (S0 에서 이미 반영)
- **완료 조건**: `python3 -c "from lib.db import init_db; init_db()"` 후 5개 테이블 존재

#### S0-4. 설정 로더
- **대상**: `lib/config.py`, `.env.example` (신규)
- `ANTHROPIC_API_KEY`, `SARAMIN_API_KEY`, `WORKNET_API_KEY`, `MODEL`
- `.env` 는 gitignore 됨. `.env.example` 만 커밋
- **완료 조건**: 키 누락 시 명확한 에러 메시지로 종료

#### S0-5. 앱 팩토리와 Blueprint 분리
- **대상**: `web/` (S1 시작 전에 수행. 이후 라우트가 계속 늘어나기 때문)
- 구조
  ```text
  web/app.py          진입점 (create_app() 호출)
  web/__init__.py     create_app(config=None), PROJECT_ROOT 는 app.config 에서 읽는다
  web/paths.py        경로 보안 헬퍼 (safe_path, user_dir, output_dir, safe_upload_name)
  web/data.py         화면용 읽기 헬퍼
  web/routes/         Blueprint 별 파일 (main, profile, documents, 이후 parse, generate, ...)
  tests/              python3 -m unittest discover -s tests -v
  ```
- 규칙: 사용자 입력이 들어가는 경로는 `web/paths.py` 헬퍼로만 만든다. 새 Blueprint 는 `web/routes/__init__.py` 의 `register_blueprints` 에 등록한다
- **완료 조건**: 분리 전후 라우트 표가 같고, S0-2 보안 회귀 테스트(`tests/test_web_security.py`)가 통과하며, `web/run.sh` 로 기동된다

---

### S1. 생성 엔진 웹 이관 (핵심)

#### S1-1. LLM 레이어
- **대상**: `lib/llm.py`, `lib/skills.py` (신규)
- `load_skill(name)` → `skills/[name].md` 를 읽어 프롬프트로 반환 (1-B 원칙)
- `stream_complete(system, messages)` → Anthropic API 스트리밍 래퍼
- **완료 조건**: `skills/write-coverletter.md` 를 로드해 자소서 1건 생성 성공

#### S1-2. 파싱 라우트
- **대상**: `web/routes/parse.py` `POST /parse/<user>` (S0-5 의 Blueprint 구조)
- `raw/` 파일 → `skills/parse-experiences.md` → `experiences/`
- 진행 상황 스트리밍 (SSE 또는 청크 응답)
- **완료 조건**: 터미널 없이 웹에서 파싱 완료

#### S1-3. 생성 라우트
- **대상**: `web/routes/generate.py` `POST /generate/<user>`
- 입력: 문항, 글자수, 기업명, 직무명
- `skills/write-coverletter.md` Step 1~10 수행 → `outputs/` 저장
- **글자수 검증을 코드로** 한다. 초과 시 재시도 1회
- **완료 조건**: 요청 글자수 대비 오차 5% 이내, 초과 0건

#### S1-4. 생성 UI
- **대상**: `web/templates/user.html`, `web/templates/generate.html` (신규)
- 문항 입력 폼, 글자수 지정, 생성 버튼, 진행 표시, 결과 표시
- **완료 조건**: 브라우저만으로 업로드 → 파싱 → 자소서 생성 완주

---

### S2. 텍스트 추출 코드화

#### S2-1. 추출기
- **대상**: `lib/extract.py` (신규)
- 지원: `.hwpx`(ZIP+XML), `.hwp`(hwp5txt), `.docx`(XML), `.pdf`, `.md`, `.txt`
- **성공과 실패를 명시적으로 반환한다.** 현재는 스킬 md 에 글로 적힌 지시라 실패해도 조용히 넘어간다
  ```python
  @dataclass
  class ExtractResult:
      ok: bool; text: str; error: str | None; method: str
  ```
- **완료 조건**: `users/채빈/raw/` 전체 파일에 대해 성공/실패가 목록으로 출력됨

#### S2-2. 실패 폴백
- **대상**: `web/templates/user.html`
- 추출 실패 파일은 "읽을 수 없습니다. 내용을 붙여넣어 주세요" 입력창 제공
- **완료 조건**: 실패 파일이 있어도 온보딩이 중단되지 않음

---

### S3. 경험 선택 분리와 대화형 발굴

#### S3-1. 선택 로직 분리
- **대상**: `lib/selector.py` (신규), `skills/write-coverletter.md`
- 현재 Step 7(경험 선택)과 Step 8(문단 작성)이 LLM 한 번에 붙어 있다.
  **선택은 코드가, 글쓰기만 LLM 이** 하도록 분리한다
- 얻는 것: 경험 직접 선택 기능, 선택 근거 표시, 경험 바꿔 재생성, 결과 재현성
- **이것이 ChatGPT 에 문항을 붙여넣는 것과의 결정적 차이다**
- **완료 조건**: 같은 문항에 경험 A, B 로 각각 생성 가능

#### S3-2. 경험 선택 UI
- **대상**: `web/templates/generate.html`
- 후보 경험 목록 + 선택 근거 + 체크박스 + 재생성 버튼
- 활동 최대 2개 제한은 유지 (`Claude.md` "경험 사용 원칙" 참고)
- **완료 조건**: 선택을 바꾸면 다른 자소서가 나옴

#### S3-3. 대화형 경험 발굴 스킬
- **대상**: `skills/elicit-experiences.md` (신규)
- raw 파일 없이 시작 가능해야 한다. 콜드 스타트 해소
- **설계 규칙**
  1. **한 번에 질문 하나.** 5개를 한꺼번에 물으면 사용자가 이탈한다
  2. 진행: 기간 구분 → 활동 나열(이름만) → 활동 하나 선택 → 역할, 행동, 성과 순 심화 → STAR 5항목 대조 → 미충족 항목만 추가 질문 → 정리본 승인 후 저장
  3. **되돌려주기**: 답변을 요약해 "이렇게 이해했습니다"로 확인받고 다음으로
  4. **추측 금지**: 말하지 않은 수치를 채우지 않는다. 빈 항목은 `⚠️ STAR 보강` 태그
  5. 한 세션 경험 1~2개. 끝에 "이어서 하시겠어요?"
- **완료 조건**: raw 파일이 하나도 없는 신규 유저로 경험 1개 생성, STAR 5항목 중 4개 이상 충족

#### S3-4. 대화형 발굴 웹 UI
- **대상**: `web/routes/elicit.py` `POST /elicit/<user>`, `web/templates/elicit.html` (신규)
- 채팅 형태. 확정된 경험은 우측에 카드로 누적
- **완료 조건**: 브라우저만으로 경험 발굴 완주

#### S3-5. writing-rules 자동 초안
- **문제(당시)**: `users/채빈/writing-rules.md` 가 템플릿 예시 그대로인데
  `write-coverletter.md` Step 8 은 이 파일을 어투 근거로 쓴다.
  어투 시스템의 절반이 빈 채로 돌아가는 중
  (※ 규칙 우선순위는 S11-0 에서 "동일 가중치" 를 폐기하고
  `Claude.md` "규칙 우선순위" 로 일원화했다)
- **대상**: `skills/parse-experiences.md`, `web/templates/setup.html`
- `writing-style.md` 의 "피해야 할 표현" 분석에서 초안을 **자동 생성**하고 사용자가 고치게 한다.
  빈 템플릿을 주지 않는다
- **완료 조건**: 실제 규칙이 채워지고 생성 결과에 반영됨

---

### S4. 공고 등록과 파싱

#### S4-1. 공고 포맷 — **[완료 2026-09-21]**
`templates/job-entry.md`. 필수조건과 우대조건을 체크박스 배열로 분리.

#### S4-2. 공고 파서
- **대상**: `lib/job_parser.py`, `skills/parse-job.md` (신규)
- 입력: URL 또는 본문 붙여넣기 → `jobs/[출처]-[ID].md` + `jobs` 테이블 행
- **핵심**: 필수조건과 우대조건을 **배열로 분리 추출**. 통짜 텍스트면 S5 매칭이 불가능
- **API 보다 먼저 하는 이유**: 수집 정책상 가장 안전한 경로(붙여넣기, 파일 업로드)만
  쓰므로 승인 대기가 없고 커버리지가 100% 다. 공고 10건이면 매칭 검증에 충분하다
  (S11-16 에서 조건부 허용 범위의 URL 수집도 추가됐다)
- **완료 조건**: 서로 다른 공고 3건에서 필수조건 배열 정상 추출

#### S4-3. 공고 등록 UI
- **대상**: `web/templates/jobs.html` (신규)
- URL 입력 또는 본문 붙여넣기, 파싱 결과 확인 후 저장
- **완료 조건**: 브라우저에서 공고 등록 완주

---

### S5. 매칭 엔진

#### S5-1. 매처
- **대상**: `lib/matcher.py`, `skills/match-jobs.md` (신규)
- ```text
  필수조건 각 항목 → 충족 / 부분충족 / 미충족 + 근거 경험 파일명
  점수 = 필수 충족률 * 0.7 + 우대 충족률 * 0.3
  적합: 필수 >= 80%   도전: 필수 50~80%   부적합: < 50%
  ```
- **근거 경험 없이 "충족" 판정 금지** (1-C)
- **완료 조건**: 공고 5건 매칭 시 모든 판정의 근거 파일이 실재함

#### S5-2. 매칭 결과 UI
- **대상**: `web/templates/matches.html` (신규)
- 표시 항목이 곧 이미지 요구사항이다
  ```text
  | 공고 | 점수 | 분류 | 추천 이유 | 연결되는 경험 | 부족한 조건 |
  ```
- 부족한 조건에는 보완 가능 여부를 함께 표시
- **완료 조건**: 적합 공고와 도전 공고가 구분되어 정렬됨

#### S5-3. 관심직무 역방향 갱신
- **대상**: `skills/match-jobs.md`
- 매칭 결과에서 실제 공고 필수조건을 역산해 `관심직무.md` 갱신을 **제안**한다.
  자동 덮어쓰기 금지

---

### S6. 채용 API 연동

#### S6-1~2. 소스 어댑터
- **대상**: `lib/sources/worknet.py`, `lib/sources/saramin.py` (신규)
- 워크넷(고용24) 채용정보 API — 공공데이터포털 `data.go.kr/data/3038225`, 무료
- 사람인 오픈 API — `oapi.saramin.co.kr`, 키 신청 필요
- 공통 인터페이스 `search(keyword, **filters) -> list[JobDTO]`
- **완료 조건**: 두 소스에서 각각 공고 20건 이상 수집

#### S6-3. 중복 제거와 마감 처리
- **대상**: `lib/dedup.py` (신규)
- `content_hash` + `normalize(company)+normalize(position)+deadline` 조합
- 같은 공고가 두 출처에 다른 ID 로 올라오므로 ID 비교만으로는 못 잡는다
- `deadline < today` → `closed=1`, 기본 목록에서 제외. 7일 이내 마감은 별도 표시
- **완료 조건**: 중복 공고가 1건으로 병합되고 마감 공고가 걸러짐

---

### S7. 서류 3종

#### S7-1. 이력서
- **대상**: `skills/write-resume.md`, `templates/resume-entry.md` (신규)
- 입력: `profile.md` + 선택 경험 + 공고 필수조건
- **창작이 아니라 재배치다.** 공고 필수조건 순서에 맞춰 경험을 재정렬하고
  해당 조건에 대응하는 성과를 앞세운다. 자소서보다 쉽다
- **완료 조건**: 같은 경험 DB 로 공고 2건에 서로 다른 순서의 이력서 생성

#### S7-2. 포트폴리오 — **범위를 좁힌다**
- **대상**: `skills/write-portfolio.md` (신규)
- 완성된 디자인 산출물은 전혀 다른 문제다.
  초기 범위는 **목차 + 각 장 텍스트 초안**까지
- 출력: `outputs/[이름]/[기업]_[직무]/portfolio-outline.md`

#### S7-3. 공고 연동 일괄 생성
- **대상**: `web/templates/generate.html`
- 공고 하나를 고르면 이력서, 자소서, 포트폴리오 초안을 한 번에 생성
- **완료 조건**: 공고 선택 → 서류 3종 생성 완주

---

### S8. 버전과 내보내기

#### S8-1. 버전 보존
- **현재 문제**: `/output/<name>/<filename>/save` 가 덮어쓴다. 이전 버전이 사라진다
- **변경**: `outputs/[이름]/[기업]_[직무]/v1.md, v2.md, ...` + `documents` 테이블 기록
- **완료 조건**: 저장 3회 후 v1, v2, v3 모두 조회 가능하고 비교 가능

#### S8-2. DOCX / PDF
- **대상**: `lib/export.py` (신규)
- DOCX 는 `python-docx`, PDF 는 DOCX 경유 또는 WeasyPrint
- 문항 제목과 글자수 표기가 출력물에 유지될 것
- **완료 조건**: 한글 폰트 깨짐 없이 열림

---

### S9. 지원 상태 관리

#### S9-1. 상태와 이력
- **대상**: `applications`, `application_events` 테이블, `web/templates/applications.html` (신규)
- 상태: `관심 → 준비중 → 지원완료 → 결과(합격/불합격/대기)`
- **반드시 상태 변경 이력을 남긴다.** "어떤 경험을 쓴 자소서가 합격했는지"를 역산할 수 있게 된다.
  **장기적으로 이 서비스에서 가장 가치 있는 데이터다**
- **완료 조건**: 상태 3회 변경 후 전체 이력 조회 가능

#### S9-2. 대시보드
- 마감 임박, 준비중, 결과 대기를 한 화면에
- **완료 조건**: 메인 화면에서 지원 현황이 한눈에 보임

---

### S10. 인증과 배포

#### S10-1. 사용자 격리
- **현재 문제**: `get_users()` 가 전체 사용자를 나열한다.
  접속만 하면 다른 사람의 자소서와 연락처, 생년월일, 학번, 학점이 전부 보인다
- 로그인, 세션, 사용자별 접근 제한, `get_users()` 노출 제거
- **완료 조건**: 로그인한 사용자의 데이터만 보임

#### S10-2. BYOK 설정 화면
- 사용자가 자기 Anthropic API 키를 입력해 자기 컴퓨터에서 실행하는 형태
- 근거: `Claude.md` 의 "데이터는 개인에게 남는다" 원칙 유지, 토큰 비용 문제 소거,
  자소서는 민감정보 덩어리라 호스팅 시 개인정보 법적 책임이 생긴다
- **완료 조건**: 키 입력 후 즉시 사용 가능

#### S10-3. 실행 패키징
- `scripts/start.sh` 하나로 의존성 설치, DB 초기화, 서버 기동
- `README.md` 를 설치 안내 중심으로 개편
- **완료 조건**: 클론 후 명령 하나로 기동

---

### S11. 생성 신뢰성 (설계검토 반영, 2026-10-02)

외부 설계검토(`자소서_자동작성_시스템_설계검토.docx`)에서 확인된 결함을 고친다.
검토의 핵심 주장: **절차서에 썼다고 지켜지지 않는다.** 순서, 필수 자료, 사용자 승인,
출력 형식은 코드가 강제하고, 논지와 직무 연결은 모델이 판단하되 검증 결과를 남긴다.

검토 A~G 중 D(우선순위)와 G-1,2,3(정책 정리 → 즉시 수정 → 공용 실행기)에 더해
품질 검사(B 축소판)와 트렌드 승인 분리(F-1)까지 수행한다.
**E(루브릭, few-shot, 피드백 학습)와 F-2(회귀 평가셋)는 보류** — 초안/최종본 쌍 0건,
레퍼런스 0건, 사용자 1명이라 지금 만들면 보정할 데이터가 없는 빈 껍데기가 된다.

#### S11-0. 정책 문서 충돌 정리
- **문제**: 스크래핑 금지가 `Claude.md` 에서만 삭제되고 `ROADMAP`, `PROMPTS`,
  `skills/parse-job.md`, `lib/job_parser.py` 에는 남아 레포가 자기모순. "writing-style 과
  동일 가중치" 가 7곳에 흩어짐. 경험 2개 제한의 출처가 gitignore 된 `HANDOFF.md`
- 외부 자료 수집 정책을 4단계(기본허용/조건부허용/금지/실패시)로 `Claude.md` 에 단일 원본화
- 규칙 우선순위를 엄격 순서로 교체, 경험 사용 원칙을 `Claude.md` 로 승격
- **완료 조건**: "동일 가중치" 와 전면 스크래핑 금지 문구가 레포에서 사라지고,
  나머지 파일이 `Claude.md` 한 곳을 인용

#### S11-1. 기업 정보 미조사 시 생성 차단
- **문제**: `companies/[기업명].md` 가 없으면 프롬프트가 "학습 데이터 기반 일반 정보 참고" 를
  지시한다. 캐시를 쓰는 코드는 있지만 **만드는 코드 경로가 없다**
- 캐시가 없으면 `error` 로 중단하고 사용자 동의를 받는다. 동의 시 열화 모드로 진행하되
  기업 고유 사실을 일절 쓰지 않고 상태를 `review_required` 로 남긴다
- `templates/company-entry.md` 에 주장 단위 `출처`/`발행일`/`확인일` 추가
- **완료 조건**: 캐시 없음, 빈 파일, 다른 기업일 때 무검증 생성이 불가능

#### S11-2. 경험 override 검증
- **문제**: `lib/selector.py` 의 `override_filenames` 가 `pick()` 을 건너뛰어 개수 상한이
  없고, 모르는 파일명을 조용히 버린다. 전부 무효면 경험 0개로 프롬프트가 나간다
- `unknown_overrides` / `dropped_overrides` 를 보고하고 호출자가 게이트한다
- **완료 조건**: 직접 API 호출로 경험 4개를 지정해도 거절되거나 2개로 잘린다

#### S11-3. 글자수 무제한 모드
- **문제**: `char_limit` 이 양의 정수만 받아 "제한 없음" 을 표현할 수 없고 상한도 없다
- `LengthSpec` 으로 `bounded`/`unlimited` 를 나눈다. 무제한이면 재시도 루프를 건너뛴다
- **완료 조건**: 제한 없음으로 생성 시 짧게 끝나지 않고, 경험을 더 늘리지도 않는다

#### S11-4. 실패 상태 분리
- **문제**: 글자수를 초과해도 `done` 을 반환하고 번들이 `coverletter_ok=True` 로 처리한다
- `done` 에 `status`(`ready_for_user`/`review_required`)와 `issues` 를 추가
- **완료 조건**: 재시도 후에도 초과한 결과가 성공으로 표시되지 않는다

#### S11-5. 프롬프트 오염 수정
- `writing-rules.md` 의 `(예: ...)` placeholder 가 실제 규칙으로 주입되는 문제
  (그중 "문항당 최대 3문장" 은 "글자수에 가깝게 채우라" 와 정면 충돌)
- `build_retry_prompt` 의 `{{company}}` 리터럴, 저장 링크의 서브폴더 누락
- **완료 조건**: `build_prompt` 출력에 placeholder 가 없고 저장 링크가 열린다

#### S11-6. 트렌드 수집본과 승인본 분리
- **문제**: `references/trends/coverletter-trends.md` 가 월 1회 자동 덮어쓰기라
  검증 없이 작성 정책이 바뀐다
- `raw/[날짜].md`(수집) / `approved.md`(승인, 프롬프트 주입 대상) / `history/` 로 분리
- **완료 조건**: 자동 갱신이 승인본을 바꾸지 못한다

#### S11-7. 생성기 추출
- **문제**: 생성 엔진이 `web/routes/generate.py` 512줄 안에 있고 `current_app` 에 묶여
  Flask 없이 import 할 수 없다. `lib/resume.py` 등 다른 생성기는 전부 Flask-free 다
- `lib/coverletter.py` 로 추출. 경로는 라우트가 해결해 `Path` 로 넘긴다
- **완료 조건**: 테스트 변경 0건으로 전체 녹색 (순수 이동)

#### S11-8. 공고 원문을 자소서에 연결
- **문제**: `generate_bundle` 이 공고 레코드 전체를 읽고도 회사명과 직무명만 넘긴다
- **완료 조건**: 공고의 실제 요구조건이 프롬프트에 들어간다

#### S11-9. 인용 경험 검증 + 초안 버전 기록
- `lib/matcher.py` 의 `_validate_evidence` 패턴으로 모델이 보고한 "참조한 경험" 을 대조
- 생성 초안도 `lib/versions.save_version` 을 거쳐 `documents` 행을 남긴다
- **완료 조건**: 코드가 고른 경험과 모델이 인용한 경험이 둘 다 보이고, 초안에 버전이 쌓인다

#### S11-10. 논지 계획과 검토 호출
- `===PLAN===` 블록을 같은 응답 앞부분에 받아 결정적 린터로 검사 (추가 호출 0회)
- `skills/review-coverletter.md` 로 별도 컨텍스트 검토 1회, 지적 기반 수정 최대 1회
- 토글로 끌 수 있게 한다. 최악의 경우 호출 4회, 끄면 2회
- **완료 조건**: 경험 2개인데 논지가 갈라진 글을 탐지한다

#### S11-17. 실행 기록 (2차 설계검토 4장, 2026-10-04)
- `runs` 테이블 + `documents.run_id`. `lib/runs.py` 가 기록하고 되묻는다
- 남기는 것: 실행 경로(web/cli/script), 워크플로, **검사한 본문의 sha256**,
  상태, 사유 목록, 의미 평가 요청 여부와 실제 수행 여부, 사용한 경험 파일
- `runs.verify(run_id, 현재본문)` 이 "검사 뒤에 본문이 바뀌었는가" 에 답한다
- 검사 통과 / 검사 실패 / **검사하지 않음** 을 `quality_checked` 로 구분해 화면에 표시
- 테스트가 기본 DB 경로로 폴백하면 `lib.db.get_conn` 이 실패한다
  (`allow_default_db()` 로만 명시적 허용)
- **완료 조건**: 손으로 저장한 문서는 `run_id` 가 비어 있어 **실행기를 거치지 않았음이
  드러난다.** 실제로 `outputs/채빈/퀴네앤드나겔_물류창고운영팀` 의 v1~v4 가 전부 NULL 이다

#### S11-18. 출처별 증명 범위 (2차 설계검토 3-1, 2026-10-04)
- 검증 출처를 `Source(kind, text)` 로 유형화. `experience` 만 **본인의 행동**을 증명한다
  (`company` 는 기업 사실, `job` 은 직무 요구조건, `glossary` 는 이름 표기만)
- `find_jd_only_facts` — 본인이 했다고 쓴 문장의 수치·기관명이 경험이 아니라 공고나
  기업 자료에만 있으면 알린다
- `jd_terms` + `check_jd_claims` — 공고에 적힌 도구(SAP, WMS 등)를 써 봤다고 말하는데
  경험 파일에 근거가 없으면 알린다. **"입사 후 익히겠습니다" 는 주장이 아니므로 통과**
- 한국어 일반명사는 NER 없이 일반적으로 뽑을 수 없다. ASCII 도구명만 본다 —
  **못 잡는 범위를 넓히는 것보다 잡는 범위를 정확히 아는 쪽을 택했다**
- **완료 조건**: 공고에 SAP 가 있고 경험에 없을 때 "SAP 로 재고를 관리했습니다" 가
  탐지되고, "입사 후 SAP 를 익히겠습니다" 는 통과한다

#### S11-19. 근거 구간 검증 (2차 설계검토 3-2, 2026-10-04)
- `===EVIDENCE===` 블록을 **같은 응답 안에서** 받는다 (추가 호출 0회, S11-10 과 같은 방식)
- 한 줄 = `주장id | 문장번호 | 주장 | 유형 | 근거 파일명 | 원문에서 그대로 옮긴 구절`
- **코드가 그 인용문이 그 파일에 실제로 있는지 대조한다** (검토 3-2 의 3단계).
  모델의 자기보고를 믿지 않는다 — `lib/matcher.py:_validate_evidence` 와 같은 원칙
- 주장과 근거의 **관계가 충분한지는 판정하지 않는다.** 그건 의미 판단이고
  `skills/review-coverletter.md` 의 검토 호출이 맡는다 (검토 3-2 의 4단계)
- 커버리지: 근거가 필요한 문장(`needs_evidence`)인데 근거 행이 없으면 알린다.
  **영향 주장**("보고서가 기준이 되었습니다")은 주어가 본인이 아니어도 포함한다 —
  가장 지어내기 쉬운 종류다. 미래 계획, 지원 동기, 해석 문장은 제외
- 계약에 `evidence_policy.require_quote` / `max_quote_chars` 추가 (S11-15 방식)
- **완료 조건**: 경험 원문에 없는 인용을 근거로 제시하면 `evidence_quote_not_found` 로
  잡히고 `review_required` 가 된다. 2026-10-02 에 실제로 썼던 거짓 주장
  ("보고서가 업무 순서의 기준이 되었다")이 시험 사례로 고정돼 있다

---

## 4. 전역 금지사항

기존 `Claude.md` 금지사항에 더해:

- 외부 자료 수집은 `Claude.md` "외부 자료 수집 정책" 의 4단계를 따른다 (1-D 참고)
- API 키를 레포에 커밋하지 않는다. `.env` 사용
- 매칭 판정에 근거 경험 파일명을 반드시 붙인다
- `skills/*.md` 를 웹용과 CLI 용으로 이원화하지 않는다
- 경험 DB 를 Markdown 에서 DB 로 옮기지 않는다
- 사용자가 말하지 않은 수치를 생성하지 않는다

---

## 5. 참고 링크

- 워크넷 채용정보 API: https://www.data.go.kr/data/3038225/openapi.do
- 고용24 Open API 소개: https://www.work24.go.kr/cm/e/a/0110/selectOpenApiIntro.do
- 사람인 채용정보 API: https://oapi.saramin.co.kr/introduce
