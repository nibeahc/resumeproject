# PROMPTS.md — CLI 실행 명령 모음

> 이 파일만 열어두고 **위에서부터 순서대로** 복사해서 Claude Code 에 붙여넣으면 된다.
> 각 프롬프트는 **자립형**이다. 이전 대화 맥락이 없어도 동작한다.
> 진행 상태는 `PROGRESS.md` 가 기억하므로, 세션이 끊겨도 이어서 할 수 있다.

---

## 0. 매 세션 시작할 때 (항상 이것부터)

```text
Claude.md, PROGRESS.md, ROADMAP.md 를 읽어줘.
그 다음 bash scripts/repo-health.sh 를 실행해서 저장소 상태를 확인해줘.
FAIL 이 있으면 먼저 해결하고, 없으면 PROGRESS.md 에서
아직 완료되지 않은 첫 번째 Task 가 무엇인지 알려줘. 아직 작업은 시작하지 마.
```

에이전트가 다음 Task 를 알려주면, 아래에서 해당 Stage 프롬프트를 찾아 붙여넣는다.
**또는 그냥 이렇게 해도 된다.**

```text
다음 Task 를 진행해줘. ROADMAP.md 의 해당 Stage 사양을 그대로 따르고,
작업 전에 무엇을 바꿀지 먼저 설명해줘. 승인하면 그때 파일을 수정해.
```

---

## 1. 매 세션 끝낼 때 (항상 이것으로 마무리)

```text
오늘 작업을 마무리하자.
1. bash scripts/repo-health.sh 실행
2. 완료한 Task 를 PROGRESS.md 에 체크하고, 다음 Task 와 남은 이슈를 기록
3. 변경 파일을 커밋 (메시지는 [Stage-Task] 형식, 예: "feat(S1-1): LLM 레이어 추가")
4. 다음 세션에서 바로 이어갈 수 있게 한 줄 요약
```

pre-commit hook 이 자동으로 저장소를 점검하므로, 문제가 있으면 커밋이 막힌다.
막히면 `bash scripts/repo-health.sh` 로 자동 복구 후 다시 커밋하면 된다.

---

# Stage 프롬프트

아래는 순서대로 진행한다. **한 번에 하나의 Stage 만.**

---

## S0. 기반 정리와 데이터 계층

### S0-2 Flask 보안

```text
ROADMAP.md 의 S0-2 를 수행해줘.

web/app.py 에 보안 3종을 적용한다.
1. debug=True 를 False 로
2. /upload/<name> 의 f.filename 에 werkzeug.utils.secure_filename() 적용
3. /output, /experience 및 각 /save 라우트에 경로 검증 헬퍼 safe_path() 적용

기존 동작을 깨뜨리지 말 것. 적용 후 서버를 띄워서
path traversal 요청이 400 을 반환하는지 직접 확인해줘.
작업 전에 변경 계획을 먼저 설명해줘.
```

### S0-3 SQLite 계층

```text
ROADMAP.md 의 S0-3 을 수행해줘.

lib/db.py 와 lib/schema.sql 을 신설하고,
ROADMAP.md 1-A 에 적힌 5개 테이블(jobs, matches, applications,
application_events, documents)을 생성하는 init_db() 를 만든다.
DB 파일 경로는 data/app.db 이고 이미 gitignore 되어 있다.

중요: 경험 DB 는 Markdown 으로 유지한다. SQLite 로 옮기지 마.
공고, 매칭, 지원현황, 문서 버전만 DB 가 관리한다.

만든 뒤 실제로 init_db() 를 실행해서 테이블 5개가 생겼는지 확인해줘.
```

### S0-4 설정 로더

```text
ROADMAP.md 의 S0-4 를 수행해줘.

lib/config.py 와 .env.example 을 신설한다.
필요한 키: ANTHROPIC_API_KEY, SARAMIN_API_KEY, WORKNET_API_KEY, MODEL
.env 는 gitignore 되어 있으니 .env.example 만 커밋한다.
키가 없을 때 무엇이 없는지 명확히 알려주는 에러 메시지를 넣어줘.
```

---

## S1. 생성 엔진 웹 이관 (가장 중요한 단계)

### S1-1 LLM 레이어

```text
ROADMAP.md 의 S1-1 을 수행해줘.

lib/llm.py 와 lib/skills.py 를 신설한다.
- load_skill(name): skills/[name].md 를 읽어 프롬프트 문자열로 반환
- stream_complete(system, messages): Anthropic API 스트리밍 래퍼

절대 원칙: skills/*.md 는 수정하지 않고 그대로 프롬프트 템플릿으로 쓴다.
프롬프트를 파이썬 코드에 하드코딩하거나 웹용으로 복제하지 마.
CLI 와 웹이 같은 스킬 파일 한 벌을 공유해야 한다.

완료되면 skills/write-coverletter.md 를 로드해서
자소서 1건을 실제로 생성해보고 결과를 보여줘.
```

### S1-2 파싱 라우트

```text
ROADMAP.md 의 S1-2 를 수행해줘.

web/routes/parse.py (Blueprint) 에 POST /parse/<user> 를 추가하고
web/routes/__init__.py 의 register_blueprints 에 등록한다.
경로를 만들 때는 web/paths.py 의 user_dir(), safe_path() 를 쓴다.
raw/ 파일을 읽어 skills/parse-experiences.md 로 파싱하고
experiences/ 에 저장한다. 진행 상황을 스트리밍으로 보여줄 것.

완료 조건: 터미널 명령 없이 웹에서만 파싱이 끝나야 한다.
```

### S1-3 생성 라우트

```text
ROADMAP.md 의 S1-3 을 수행해줘.

web/routes/generate.py (Blueprint) 에 POST /generate/<user> 를 추가하고 등록한다.
경로를 만들 때는 web/paths.py 의 user_dir(), output_dir(), safe_path() 를 쓴다.
입력은 문항, 글자수, 기업명, 직무명.
skills/write-coverletter.md 의 Step 1~10 을 수행하고 outputs/ 에 저장한다.

중요: 글자수 검증을 LLM 에 맡기지 말고 코드로 해라.
초과하면 1회 재시도하고, 그래도 초과면 사용자에게 알린다.
공백 포함이 기본값이다.

완료 후 여러 글자수(500, 800, 1000자)로 테스트해서
오차와 초과 건수를 보고해줘.
```

### S1-4 생성 UI

```text
ROADMAP.md 의 S1-4 를 수행해줘.

web/templates/generate.html 을 신설하고 user.html 에서 연결한다.
문항 입력, 글자수 지정, 생성 버튼, 진행 표시, 결과 표시.
기존 템플릿들(base.html, user.html)의 디자인 톤을 따라줘.

완료 조건: 브라우저만으로 업로드 → 파싱 → 자소서 생성이 완주되어야 한다.
이게 되면 이 프로젝트는 비로소 서비스가 된다. 직접 끝까지 해보고 보고해줘.
```

---

## S2. 텍스트 추출 코드화

```text
ROADMAP.md 의 S2-1, S2-2 를 수행해줘.

lib/extract.py 를 신설한다.
.hwpx, .hwp, .docx, .pdf, .md, .txt 에서 텍스트를 추출하되,
성공과 실패를 명시적으로 반환하는 ExtractResult(ok, text, error, method) 형태로.

지금은 이 변환이 스킬 md 에 글로 적힌 지시라서 실패해도 조용히 넘어간다.
이게 온보딩 실패의 주원인이다.

만든 뒤 users/채빈/raw/ 의 모든 파일에 대해 돌려서
성공과 실패를 목록으로 보여줘. 실패한 포맷이 있으면 원인도 알려줘.
그 다음 web/templates/user.html 에 실패 파일 붙여넣기 폴백 UI 를 추가한다.
```

---

## S3. 경험 선택 분리와 대화형 발굴

### S3-1, S3-2 경험 선택 분리

```text
ROADMAP.md 의 S3-1, S3-2 를 수행해줘.

지금 skills/write-coverletter.md 의 Step 7(경험 선택)과
Step 8(문단 작성)이 LLM 호출 한 번에 붙어 있다.
이걸 분리한다. 선택은 lib/selector.py 가 코드로, 글쓰기만 LLM 이.

얻는 것: 사용자가 경험을 직접 고를 수 있고, 왜 그 경험이 뽑혔는지 보여줄 수 있고,
경험을 바꿔 재생성할 수 있고, 결과가 재현 가능해진다.
이게 ChatGPT 에 문항 붙여넣는 것과의 결정적 차이다.

그 다음 generate.html 에 후보 경험 목록, 선택 근거, 체크박스, 재생성 버튼을 붙인다.
활동 최대 2개 제한은 유지한다.

완료 후 같은 문항에 서로 다른 경험을 골라 두 버전을 생성해서 보여줘.
```

### S3-3, S3-4 대화형 경험 발굴

```text
ROADMAP.md 의 S3-3, S3-4 를 수행해줘.

skills/elicit-experiences.md 를 신설한다.
raw 파일이 하나도 없어도 대화만으로 경험 DB 를 만들 수 있어야 한다.

설계 규칙 5개를 반드시 지켜라.
1. 한 번에 질문 하나. 5개를 한꺼번에 물으면 사용자가 이탈한다
2. 순서: 기간 구분 → 활동 나열(이름만) → 활동 하나 선택 →
   역할, 행동, 성과 순 심화 → STAR 5항목 대조 → 미충족 항목만 추가 질문 →
   정리본 승인 후 저장
3. 사용자 답변을 요약해 "이렇게 이해했습니다"로 확인받고 다음 질문으로
4. 말하지 않은 수치를 채우지 마라. 빈 항목은 ⚠️ STAR 보강 태그로 남긴다
5. 한 세션에 경험 1~2개까지

기존 skills/*.md 의 문서 구조와 톤을 그대로 따라줘.
그 다음 web/routes/elicit.py 의 POST /elicit/<user> 와 web/templates/elicit.html 로 웹 UI 를 만든다.

완료 후 테스트용 신규 유저를 만들어 실제로 경험 1개를 대화로 뽑아보고,
STAR 5항목 중 몇 개가 채워졌는지 보고해줘.
```

### S3-5 writing-rules 자동 초안

```text
ROADMAP.md 의 S3-5 를 수행해줘.

문제: users/채빈/writing-rules.md 가 템플릿 예시 그대로 비어 있다.
그런데 skills/write-coverletter.md Step 8 은 이 파일을 어투 근거로 쓴다.
어투 시스템의 절반이 빈 채로 돌아가는 중이다.
(※ 이 Task 는 2026-09-23 완료. 규칙 우선순위는 Claude.md "규칙 우선순위" 가 정본이다)

skills/parse-experiences.md 를 고쳐서,
writing-style.md 의 "피해야 할 표현" 분석으로부터
writing-rules.md 초안을 자동 생성하게 한다. 빈 템플릿을 주지 마라.
그 다음 web/templates/setup.html 에 작성 규칙 입력 섹션을 추가한다.

완료 후 채빈 계정의 writing-rules.md 를 실제로 채우고,
채우기 전후로 같은 문항을 생성해서 결과가 어떻게 달라지는지 비교해줘.
```

---

## S4. 공고 등록과 파싱

```text
ROADMAP.md 의 S4-2, S4-3 을 수행해줘.

lib/job_parser.py 와 skills/parse-job.md 를 신설한다.
공고 URL 또는 본문을 붙여넣으면 templates/job-entry.md 형식으로
jobs/ 에 저장하고 jobs 테이블에도 행을 넣는다.

가장 중요한 것: 필수조건과 우대조건을 배열로 분리 추출해야 한다.
통짜 텍스트로 두면 다음 단계인 매칭 엔진이 아예 동작하지 않는다.

채용 API 연동은 아직 하지 마. 붙여넣기만으로 먼저 완성한다.
법적 리스크가 없고 커버리지가 100%라 매칭 검증에는 이게 더 낫다.

그 다음 web/templates/jobs.html 로 등록 화면을 만든다.
완료 후 실제 공고 3건을 등록해서 필수조건 배열이 제대로 나오는지 보여줘.
```

---

## S5. 매칭 엔진

```text
ROADMAP.md 의 S5-1, S5-2, S5-3 을 수행해줘.

lib/matcher.py 와 skills/match-jobs.md 를 신설한다.

판정 방식:
  필수조건 각 항목 → 충족 / 부분충족 / 미충족 + 근거 경험 파일명
  점수 = 필수 충족률 * 0.7 + 우대 충족률 * 0.3
  적합 >= 80%, 도전 50~80%, 부적합 < 50%

절대 규칙: 근거 경험 파일명 없이 "충족" 으로 판정하지 마라.
환각을 막는 핵심 장치다. 근거 파일은 실재하는 경로여야 한다.

출력 항목은 이것으로 고정한다.
  공고 | 점수 | 분류 | 추천 이유 | 연결되는 경험 | 부족한 조건

그 다음 web/templates/matches.html 을 만든다.
부족한 조건에는 보완 가능 여부도 함께 표시한다.

완료 후 공고 5건을 매칭하고, 모든 판정의 근거 파일이
실제로 존재하는지 검증한 결과를 보고해줘.
```

---

## S6. 채용 API 연동

```text
ROADMAP.md 의 S6-1, S6-2, S6-3 을 수행해줘.

lib/sources/worknet.py 와 lib/sources/saramin.py 를 신설한다.
- 워크넷(고용24): 공공데이터포털 https://www.data.go.kr/data/3038225/openapi.do , 무료
- 사람인: https://oapi.saramin.co.kr/introduce , 키 신청 필요

외부 자료 수집은 Claude.md 의 "외부 자료 수집 정책" 4단계를 따른다.
이 Task 에서는 공식 API 만 쓴다 — 로그인·캡차 우회나 대량 수집은 금지 구간이다.
API 키는 .env 에서 읽고 절대 커밋하지 않는다.

그 다음 lib/dedup.py 로 중복을 제거한다.
같은 공고가 두 출처에 다른 ID 로 올라오므로 ID 비교로는 못 잡는다.
content_hash + 정규화한 기업명, 직무명, 마감일 조합으로 판정한다.
마감일이 지난 공고는 closed=1 로 하고 기본 목록에서 뺀다.
7일 이내 마감은 별도 표시한다.

API 키가 아직 없으면 어디서 신청하는지 알려주고 어댑터 구조만 먼저 만들어줘.
```

---

## S7. 서류 3종

```text
ROADMAP.md 의 S7-1, S7-2, S7-3 을 수행해줘.

1) skills/write-resume.md 와 templates/resume-entry.md 신설.
   이력서는 창작이 아니라 재배치다. 공고 필수조건 순서에 맞춰
   경험을 재정렬하고 해당 조건에 대응하는 성과를 앞세운다.

2) skills/write-portfolio.md 신설.
   범위를 좁힌다. 완성된 디자인 산출물은 만들지 않는다.
   목차와 각 장 텍스트 초안까지만.

3) generate.html 에서 공고 하나를 고르면
   이력서, 자소서, 포트폴리오 초안이 한 번에 생성되게 연결한다.

완료 후 같은 경험 DB 로 서로 다른 공고 2건에 대해 이력서를 만들어
순서가 실제로 달라지는지 보여줘.
```

---

## S8. 버전과 내보내기

```text
ROADMAP.md 의 S8-1, S8-2 를 수행해줘.

지금 /output/<name>/<filename>/save 는 덮어쓴다. 이전 버전이 사라진다.
outputs/[이름]/[기업]_[직무]/v1.md, v2.md 구조로 바꾸고
documents 테이블에 기록한다. 버전 간 비교 화면도 만든다.

그 다음 lib/export.py 로 DOCX 와 PDF 내보내기를 만든다.
DOCX 는 python-docx, PDF 는 DOCX 경유 또는 WeasyPrint.
문항 제목과 글자수 표기가 출력물에 남아야 한다.

완료 후 실제로 DOCX 를 만들어서 한글 폰트가 깨지지 않는지 확인해줘.
```

---

## S9. 지원 상태 관리

```text
ROADMAP.md 의 S9-1, S9-2 를 수행해줘.

applications 와 application_events 테이블을 쓰고
web/templates/applications.html 을 만든다.
상태: 관심 → 준비중 → 지원완료 → 결과(합격/불합격/대기)

반드시 상태 변경 이력을 남겨라.
나중에 "어떤 경험을 쓴 자소서가 합격했는지" 를 역산할 수 있게 된다.
장기적으로 이 서비스에서 가장 가치 있는 데이터다.

그 다음 대시보드를 만든다. 마감 임박, 준비중, 결과 대기를 한 화면에.
완료 후 공고 1건의 상태를 3번 바꾸고 이력 전체를 조회해서 보여줘.
```

---

## S10. 인증과 배포

```text
ROADMAP.md 의 S10-1, S10-2, S10-3 을 수행해줘.

1) 지금 get_users() 가 전체 사용자를 나열한다.
   접속만 하면 다른 사람의 자소서와 연락처, 생년월일, 학번, 학점이 다 보인다.
   로그인과 세션을 붙이고 사용자별 접근을 제한한다.

2) BYOK 설정 화면. 사용자가 자기 Anthropic API 키를 넣고
   자기 컴퓨터에서 돌리는 형태를 먼저 완성한다.
   데이터가 개인에게 남는다는 원칙이 유지되고, 토큰 비용 문제가 사라지고,
   자소서가 민감정보라 호스팅 시 생기는 법적 책임도 피한다.

3) scripts/start.sh 하나로 의존성 설치, DB 초기화, 서버 기동이 되게 한다.
   README.md 를 설치 안내 중심으로 개편한다.

완료 후 클론부터 기동까지를 처음부터 한 번 돌려보고 보고해줘.
```

---

# 막혔을 때

## 저장소가 이상할 때

```text
bash scripts/repo-health.sh 를 실행하고 결과를 해석해줘.
FAIL 이 있으면 어떻게 고칠지 설명하고, 안전한 조치부터 순서대로 해줘.
```

## 파일을 못 읽는다고 할 때 (Resource deadlock avoided)

iCloud 가 파일을 내려보낸 상태다. 터미널에서 직접:

```bash
brctl download "/Users/chaebin/Documents/DIVE/Claude CLI/ResumeProject"
```

또는 Finder 에서 폴더 우클릭 → 지금 다운로드.

## git 명령이 멈출 때

```bash
rm -f .git/index.lock && bash scripts/repo-health.sh
```

## 방향을 잃었을 때

```text
ROADMAP.md 의 "완성 정의" 10단계 중에서
지금 몇 번까지 실제로 동작하는지 직접 확인해서 알려줘.
동작하지 않는 첫 단계가 다음에 할 일이다.
```
