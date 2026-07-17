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
  writing-rules.md  ← 사용자가 직접 설정한 작성 규칙 (writing-style.md와 동일 가중치)
  index.md          ← 전체 경험 키워드/카테고리 색인
  관심직무.md        ← 관심 직무 목록, 직무별 핵심 키워드·추천 표현·피해야 할 표현

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
- write-coverletter            : 자소서 문항 → 문단 작성 (기업 리서치 + 레퍼런스 자동 참조)
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
- 해당 사용자의 writing-style.md 어투를 반드시 따를 것
- 글자수 제한이 주어지면 반드시 지킬 것

## 출력 규칙

- 자소서 답변은 문단(paragraph) 형식으로 작성
- 글자수가 주어지면 해당 글자수 이내로 맞출 것
- 글자수가 주어지지 않으면 작성 전에 먼저 글자수를 물어볼 것
- 초안 하단에 "참조한 경험: [경험명]" 명시
- 이력 조회: 표 형식 (활동명 / 기간 / 기관명 / 역할 / 활동내용 요약)
