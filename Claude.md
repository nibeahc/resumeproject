# 자소서 자동 작성 프로젝트

## 이 프로젝트의 목적

여러 사용자의 자소서 데이터를 기반으로

1. 사용자의 경험을 구조화된 DB로 정리한다
2. 자소서 문항과 글자수를 던지면 해당 사용자의 어투로 문단을 작성한다
3. 이력을 명령어로 조회한다

## 폴더 구조

```text
users/[이름]/
  raw/
    apply/          ← 자소서, 지원서, 포트폴리오 등 지원/신청 문서
    logs/           ← 소감문, 활동일지, 결과보고서 등 활동 기록 문서
  experiences/      ← 구조화된 경험 DB
  profile.md        ← 기본 인적사항, 가치관, 성격
  writing-style.md  ← 어투/문체 분석 결과
  writing-rules.md  ← 사용자가 직접 설정한 작성 규칙 (writing-style.md 보다 우선)
  index.md          ← 전체 경험 키워드/카테고리 색인
  관심직무.md        ← 관심 직무 목록, 직무별 핵심 키워드·추천 표현·피해야 할 표현
  glossary.md       ← 외부 독자가 모르는 고유명사와 자소서용 설명형 표현

companies/[기업명].md  ← 기업 정보 캐시 (자소서 작성 시 MCP로 자동 수집·저장)

references/
  coverletter/[직무명]/  ← 합격 자소서 레퍼런스 (사용자 직접 추가 또는 MCP 수집)

skills/             ← 공용 작업 절차 파일들
templates/          ← 공용 출력 형식 템플릿
  user-skeleton/    ← 새 사용자 추가용 빈 파일 세트
outputs/[이름]/     ← 사용자별 생성 결과물
  [기업명]_[직무명].md      ← 자소서
  면접_[기업명]_[직무명].md ← 면접 질문
```

## Skills (작업 절차)

- parse-experiences            : users/[이름]/raw/ → users/[이름]/experiences/ 변환 및 태깅
- elicit-experiences           : raw 파일 없이 대화로 경험 발굴 (콜드 스타트)
- write-coverletter            : 자소서 문항 → 문단 작성 (기업 리서치 + 레퍼런스 자동 참조)
- review-coverletter           : 작성된 초안의 문장별 역할·근거 검토 (S11-10, 별도 호출)
- write-resume                 : 공고 필수조건 순서로 경험 재배치 (창작 아님)
- write-portfolio              : 포트폴리오 목차 + 챕터 초안
- parse-job                    : 붙여넣은 채용공고 → jobs/[출처]-[ID].md + jobs 테이블
- match-jobs                   : 공고 조건과 경험 DB 대조 (근거 파일명 필수)
- query-experiences            : 이력 조회
- generate-interview-questions : 자소서 기반 면접 예상 질문 생성

## 명령어

- "[이름] 파싱해줘"
  → skills/parse-experiences.md 실행
- "[이름] [기업명] [직무명] 자소서 써줘: [문항] / [글자수]"
  → skills/write-coverletter.md 실행 (기업 정보·직무 톤·레퍼런스 자동 참조)
- "[이름] 이력 보여줘"
  → skills/query-experiences.md 실행 (전체 조회, 카테고리별)
- "[이름] 이력 타임라인 보여줘"
  → skills/query-experiences.md 실행 (전체 조회, 연도별 타임라인)
- "[이름] [키워드] 관련 이력 보여줘"
  → skills/query-experiences.md 실행 (필터 조회)
- "[이름] [직무명] 직무로 이력 보여줘"
  → skills/query-experiences.md 실행 (직무 기반 관련도 순 조회)
- "[이름] [기업명] [직무명] 면접 질문 뽑아줘"
  → skills/generate-interview-questions.md 실행

## 새 사용자 추가 방법

1. templates/user-skeleton/ 폴더를 users/[이름]/ 으로 복사
2. users/[이름]/profile.md 기본 정보 작성
3. users/[이름]/관심직무.md 관심 직무 작성
4. users/[이름]/raw/apply/ 에 자소서/지원서 넣기
5. users/[이름]/raw/logs/ 에 소감문/활동일지 넣기 (선택)
6. "[이름] 파싱해줘" 실행

## 경험 파일 형식

모든 experiences/ 파일은 templates/experience-entry.md 형식을 따른다

## 절대 금지

- 경험을 지어내거나 과장하지 말 것
- raw/ 파일을 수정하거나 삭제하지 말 것
- 해당 사용자의 writing-rules.md 와 writing-style.md 를 반드시 따를 것
- 글자수 제한이 주어지면 반드시 지킬 것
- 한 문항에 활동을 3개 이상 나열하지 말 것

## 규칙 우선순위

충돌하면 위가 이긴다. 아래쪽으로 위쪽을 덮어쓸 수 없다.

1. **불변 제약** — 사실성, 개인정보, 글자수 제한, 위 "절대 금지" 항목
2. **이번 요청에서 사용자가 명시한 지시**
3. `users/[이름]/writing-rules.md` — 사용자가 직접 승인한 규칙
4. `users/[이름]/writing-style.md` — 관찰로 추론한 문체 선호
5. 공용 작성 기본값 (`skills/*.md`)
6. `references/trends/approved.md` — 승인된 트렌드

이번 지시가 과거 규칙과 다르면 "이번 작업만의 예외" 인지 "영구 규칙 변경" 인지 구분해
확인한다. 불변 제약은 예외로 덮을 수 없다. 상충하는 명시 규칙을 조용히 절충하지 않는다.

## 경험 사용 원칙

- 한 문항에 활동은 **최대 2개, 기본 1개** 를 쓴다
- 여러 활동을 나열하지 않는다. 하나를 깊이 풀어낸다
- 2개를 쓸 때는 두 번째가 같은 주장을 보강하거나, 대조하거나, 변화 과정을 보여야 한다.
  "둘 다 분석 경험이다" 는 두 번째를 넣을 이유가 아니다

이 규칙의 정식 출처는 이 절이다. `ROADMAP.md`, `skills/write-coverletter.md`,
`lib/selector.py`, `web/templates/generate.html` 은 여기를 인용한다.

## 출력 규칙

- 자소서 답변은 문단(paragraph) 형식으로 작성
- 글자수가 주어지면 해당 글자수 이내로 맞출 것
- 글자수가 주어지지 않으면 작성 전에 먼저 글자수를 물어볼 것
- 초안 하단에 "참조한 경험: [경험명]" 명시
- 이력 조회: 표 형식 (활동명 / 기간 / 기관명 / 역할 / 활동내용 요약)

---

## 세션 프로토콜 (2026-09-21 추가, 모든 세션에서 강제)

이 레포는 **iCloud Drive 안에** 있다. iCloud 가 `.git` 내부 파일을 evict 하면
git 이 깨진다. 과거의 "파일 소실", `index.lock` 잠금, 1.01GB 중단된 임시 pack 사고가
전부 이 원인이었다. 그래서 아래를 자동으로 수행한다.

### 세션 시작 시 (예외 없이)

1. `PROGRESS.md` 를 읽어 다음 Task 를 확인한다
2. `bash scripts/repo-health.sh` 를 실행한다
3. FAIL 이 있으면 **작업을 시작하기 전에** 먼저 해결한다

### 세션 종료 시 (예외 없이)

1. `bash scripts/repo-health.sh` 를 실행한다
2. `PROGRESS.md` 의 체크박스와 "마지막 세션" 란을 갱신한다
3. 커밋한다. 메시지 형식은 `타입(Stage-Task): 요약`
   (예: `feat(S1-1): LLM 레이어 추가`)

`PROGRESS.md` 를 갱신하지 않으면 다음 세션이 같은 일을 반복한다.
이 갱신은 선택이 아니라 작업의 일부다.

### 자동화된 것

- `.githooks/pre-commit` — 커밋 직전 점검. 개인정보 추적이나 저장소 손상이 있으면 커밋을 막는다
- `.githooks/post-merge`, `.githooks/post-checkout` — pull 과 브랜치 전환 후 자동 점검 및 복구
- `git config core.hooksPath .githooks` 로 활성화되어 있다

`scripts/repo-health.sh` 가 점검하는 것: index.lock, 중단된 임시 pack,
git fsck 무결성, iCloud evict 여부, 개인정보 추적 여부, 워킹 트리에서 사라진 추적 파일.
`--check` 옵션은 점검만 하고 복구하지 않는다 (hook 전용).

---

## 계획 문서 체계 (2026-09-21 추가)

| 파일 | 역할 | 누가 갱신하나 |
|---|---|---|
| `Claude.md` | 룰베이스. 지켜야 할 규칙 | 규칙이 바뀔 때만 |
| `PROGRESS.md` | 어디까지 했는가 | **매 세션 에이전트가 갱신** |
| `ROADMAP.md` | 무엇을 만드는가 (S0~S10 사양) | 사양이 바뀔 때 |
| `PROMPTS.md` | 어떻게 시작하는가 (CLI 명령) | 거의 안 바뀜 |
| `HANDOFF.md` | 현재 상태 스냅샷 | 큰 변화가 있을 때 |

목표는 **지원 파이프라인 서비스 완성**이다.
완성 정의 10단계는 `ROADMAP.md` 0장에 있다.

### 이 프로젝트에서 절대 하지 않는 것 (추가분)

- API 키를 레포에 커밋하는 것. `.env` 를 쓴다
- 근거 경험 파일명 없이 공고 조건을 "충족" 으로 판정하는 것
- `skills/*.md` 를 웹용과 CLI 용으로 이원화하는 것. 한 벌만 유지한다
- 경험 DB 를 Markdown 에서 DB 로 옮기는 것. Markdown 이 정본이고, SQLite 는
  공고·매칭·지원현황·문서버전 메타데이터만 관리한다 (`lib/schema.sql`)

## 외부 자료 수집 정책

"스크래핑" 이라는 한 단어로 모든 행위를 묶지 않는다. 목적과 접근 방식으로 나눈다.
**이 절이 이 정책의 유일한 원본이다.** `ROADMAP.md`, `PROMPTS.md`, `skills/parse-job.md`,
`lib/job_parser.py` 는 규칙을 복제하지 말고 여기를 인용한다.

| 구분 | 내용 |
|---|---|
| **기본 허용** | 사용자가 붙여넣은 본문과 업로드 파일, 공식 API, 모델 제공자의 내장 검색 도구 |
| **조건부 허용** | 사이트 접근 정책(robots.txt, 이용약관)을 확인한 **공개 URL 의 단건 수집**. 출처 URL 과 수집 시점을 기록하고, 캐시하고, 요청 간격을 둔다 |
| **금지** | 로그인·캡차·접근제한 우회, 제한된 페이지의 무단 수집, 허가되지 않은 반복·대량 수집 |
| **실패 시** | 추론해서 채우지 않는다. 사용자에게 붙여넣기나 공식 원문 업로드를 요청한다 |

공개 페이지라는 이유만으로 모든 수집이 허용되지는 않는다. 위 분류는 법률 판단을 대신하지
않으며, 사이트별로 따로 확인한다.
