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

### 1-D. 채용 사이트 스크래핑 금지

공식 API 또는 사용자가 붙여넣은 본문만 사용한다.
사람인, 잡코리아 직접 크롤링은 이용약관 위반이고 로그인과 캡차로 계속 깨진다.

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
- 활동 최대 2개 제한은 유지 (기존 규칙)
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
- **문제**: `users/채빈/writing-rules.md` 가 템플릿 예시 그대로인데
  `write-coverletter.md` Step 8 은 이 파일을 `writing-style.md` 와 **동일 가중치로** 쓴다.
  어투 시스템의 절반이 빈 채로 돌아가는 중
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
- **API 보다 먼저 하는 이유**: 법적 리스크 0, 커버리지 100%, 승인 대기 불필요.
  공고 10건이면 매칭 검증에 충분하다
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

## 4. 전역 금지사항

기존 `Claude.md` 금지사항에 더해:

- 채용 사이트 스크래핑 금지. 공식 API 또는 사용자 붙여넣기만
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
