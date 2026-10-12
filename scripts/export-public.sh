#!/bin/bash
# export-public.sh — 공개용 resumeproject 로 코드만 내보낸다.
#
# 개인정보(users/, outputs/, companies/)와 비밀/서비스데이터(.env, data/, jobs/, *.db)는
# 절대 포함하지 않는다. "정기 자동" 이 아니라 요청받을 때만 수동 실행한다.
#
# 사용법: bash scripts/export-public.sh
# 이 스크립트는 파일 복사 + git add + commit 까지만 한다. push 는 결과를
# 확인한 뒤 공개 레포에서 직접 한다 (git push).

set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

PUBLIC_REPO="/Users/chaebin/Documents/DIVE/Claude CLI/ResumeProject"

if [ ! -d "$PUBLIC_REPO/.git" ]; then
  echo "공개 레포를 찾을 수 없음: $PUBLIC_REPO"
  exit 1
fi

echo "private → public 코드 동기화 중..."

# .gitignore 와 scripts/repo-health.sh 는 레포마다 다르게 유지해야 한다 —
# 공개 레포 쪽은 users/outputs/companies/data/jobs 를 막는 원본 버전을 그대로
# 둬야 하므로, 여기서 덮어쓰지 않는다.
# Claude.md 도 마찬가지다 — 레포 구조 절이 레포마다 다르다("이 레포는 작업용" vs
# "이 레포는 공개용"). 공통 규칙을 고쳤다면 공개 레포 쪽 Claude.md 에 손으로 옮긴다.
# --delete 는 쓰지 않는다 (공개 레포에서 파일이 자동으로 지워지는 걸 막기 위해).
# private 에서 파일을 지운 경우, 공개 레포에서 git rm 을 수동으로 해야 한다.
rsync -a \
  --exclude='.git/' \
  --exclude='.gitignore' \
  --exclude='Claude.md' \
  --exclude='scripts/repo-health.sh' \
  --exclude='.env' --exclude='.env.example' \
  --exclude='users/' --exclude='outputs/' --exclude='companies/' \
  --exclude='data/' --exclude='jobs/' \
  --exclude='.venv/' --exclude='__pycache__/' --exclude='.pytest_cache/' \
  --exclude='_trash/' --exclude='.DS_Store' --exclude='*.db' \
  --exclude='HANDOFF.md' \
  --exclude='record/development report*.md' \
  --exclude='record/handoff_*.md' \
  --exclude='record/세션기록_*.md' \
  ./ "$PUBLIC_REPO"/

cd "$PUBLIC_REPO" || exit 1

echo ""
echo "공개 레포 변경사항:"
git status --short

echo ""
echo "변경사항을 커밋하려면:"
echo "  cd \"$PUBLIC_REPO\""
echo "  git add -A && git commit -m \"sync: private 작업본에서 코드 반영\""
echo "  git push"
