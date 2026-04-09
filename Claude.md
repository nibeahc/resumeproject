# 자소서 자동 작성 프로젝트

## 이 프로젝트의 목적
여러 사용자의 자소서 데이터를 기반으로
1. 사용자의 경험을 구조화된 DB로 정리한다
2. 자소서 문항과 글자수를 던지면 해당 사용자의 어투로 문단을 작성한다
3. 이력을 명령어로 조회한다

## 폴더 구조
users/[이름]/
  raw/              ← 원본 자소서 글 뭉탱이 (건드리지 않음)
  experiences/      ← 구조화된 경험 DB
  profile.md        ← 기본 인적사항, 가치관, 성격
  writing-style.md  ← 어투/문체 분석 결과
  index.md          ← 전체 경험 키워드/카테고리 색인

skills/             ← 공용 작업 절차 파일들
templates/          ← 공용 출력 형식 템플릿
outputs/[이름]/     ← 사용자별 생성 결과물

## Skills (작업 절차)
- parse-experiences  : users/[이름]/raw/ → users/[이름]/experiences/ 변환 및 태깅
- write-coverletter  : 자소서 문항 → 문단 작성
- query-experiences  : 이력 조회

## 명령어
- "[이름] 파싱해줘"
  → skills/parse-experiences.md 실행
- "[이름] 자소서 써줘: [문항] / [글자수]"
  → skills/write-coverletter.md 실행
- "[이름] 이력 보여줘"
  → skills/query-experiences.md 실행 (전체 조회)
- "[이름] [키워드] 관련 이력 보여줘"
  → skills/query-experiences.md 실행 (필터 조회)

## 새 사용자 추가 방법
1. users/[이름]/ 폴더 생성
2. users/[이름]/raw/ 에 기존 자소서 넣기
3. "[이름] 파싱해줘" 실행

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