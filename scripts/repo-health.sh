#!/bin/bash
# repo-health.sh — 저장소 건강 점검 및 자동 복구
#
# 이 레포는 iCloud Drive 안에 있다. iCloud 가 .git 내부 파일을 evict 하면
# git 이 깨지고, 그 결과가 과거의 "파일 소실", index.lock 잠금,
# 1.01GB 중단된 임시 pack 사고였다.
#
# 사용법:
#   bash scripts/repo-health.sh          점검 + 자동 복구
#   bash scripts/repo-health.sh --check  점검만 (복구 안 함, CI/hook 용)
#
# 종료 코드: 0 정상 / 1 사람이 봐야 하는 문제 발견

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

RED=$'\033[31m'; YEL=$'\033[33m'; GRN=$'\033[32m'; RST=$'\033[0m'
PROBLEM=0

say()  { echo "  $*"; }
ok()   { echo "  ${GRN}OK${RST}   $*"; }
warn() { echo "  ${YEL}WARN${RST} $*"; }
bad()  { echo "  ${RED}FAIL${RST} $*"; PROBLEM=1; }

echo "=============================================="
echo " repo-health  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=============================================="

# ── 1. index.lock ────────────────────────────────────────────────────────────
echo "[1/6] index.lock"
if [ -f .git/index.lock ]; then
  AGE=$(( $(date +%s) - $(stat -f %m .git/index.lock 2>/dev/null || stat -c %Y .git/index.lock) ))
  if [ "$CHECK_ONLY" -eq 1 ]; then
    bad "index.lock 존재 (${AGE}초 경과). git 명령이 막힌다"
  elif [ "$AGE" -gt 120 ]; then
    rm -f .git/index.lock && warn "index.lock 제거함 (${AGE}초간 방치된 잠금)"
  else
    warn "index.lock 이 방금 생겼다 (${AGE}초). 다른 git 작업 진행 중일 수 있어 두었다"
  fi
else
  ok "없음"
fi

# ── 2. 중단된 임시 pack (1GB 사고의 원인) ────────────────────────────────────
echo "[2/6] 중단된 임시 pack"
TMPPACKS=$(find .git/objects/pack -name 'tmp_pack_*' 2>/dev/null)
if [ -n "$TMPPACKS" ]; then
  SIZE=$(du -sh .git/objects/pack 2>/dev/null | cut -f1)
  if [ "$CHECK_ONLY" -eq 1 ]; then
    bad "tmp_pack 잔해 발견 (pack 폴더 ${SIZE})"
  else
    echo "$TMPPACKS" | while read -r f; do rm -f "$f" && warn "제거: $f"; done
    git gc --prune=now --quiet 2>/dev/null
    ok "정리 완료 → .git $(du -sh .git | cut -f1)"
  fi
else
  ok "없음 (.git $(du -sh .git | cut -f1))"
fi

# ── 3. git 객체 무결성 ───────────────────────────────────────────────────────
echo "[3/6] git fsck"
# dangling / unreachable 객체는 정상이다 (add 후 reset 등으로 늘 생긴다).
# missing, broken, corrupt 만 실제 손상이다.
FSCK_RAW=$(git fsck --no-progress --connectivity-only 2>&1)
FSCK=$(echo "$FSCK_RAW" | grep -viE '^(checking|notice:|dangling |unreachable )' | grep -v '^$' | head -20)
DANGLING=$(echo "$FSCK_RAW" | grep -cE '^(dangling|unreachable) ' | tr -d ' ')
if [ -n "$FSCK" ]; then
  bad "무결성 이상:"; echo "$FSCK" | sed 's/^/       /'
elif [ "${DANGLING:-0}" -gt 0 ]; then
  ok "이상 없음 (dangling 객체 ${DANGLING}개는 정상. git gc 로 정리됨)"
else
  ok "이상 없음"
fi

# ── 4. iCloud evict 감지 및 복구 ─────────────────────────────────────────────
# evict 된 파일은 ".파일명.icloud" 플레이스홀더로 바뀐다.
# 이 상태에서 파일을 읽으면 "Resource deadlock avoided" 로 실패한다.
echo "[4/6] iCloud evict"
EVICTED=$(find . -name '*.icloud' -not -path './.git/*' 2>/dev/null | head -50)
EVICTED_GIT=$(find .git -name '*.icloud' 2>/dev/null | head -10)
if [ -n "$EVICTED_GIT" ]; then
  bad ".git 내부 파일이 evict 됐다. 이 상태로 git 을 쓰면 저장소가 깨진다"
  say "복구: brctl download .git   (또는 Finder 에서 폴더 우클릭 → 지금 다운로드)"
elif [ -n "$EVICTED" ]; then
  N=$(echo "$EVICTED" | wc -l | tr -d ' ')
  if [ "$CHECK_ONLY" -eq 1 ]; then
    warn "${N}개 파일이 클라우드에만 있다 (로컬 미다운로드)"
  elif command -v brctl >/dev/null 2>&1; then
    warn "${N}개 evict 파일 발견. 다운로드 시도 중..."
    brctl download . >/dev/null 2>&1 && ok "다운로드 요청 완료 (완료까지 잠시 걸릴 수 있음)"
  else
    warn "${N}개 evict 파일. brctl 없음 → Finder 에서 수동 다운로드 필요"
  fi
else
  ok "전부 로컬에 있음"
fi

# ── 5. 개인정보 누출 방어 ────────────────────────────────────────────────────
echo "[5/6] 개인정보 추적 여부"
# core.quotepath=false 가 없으면 한글 경로가 "users/\354..." 로 인코딩되어 매칭에 실패한다
LEAK=$(git -c core.quotepath=false ls-files 2>/dev/null | grep -E '^"?(users/|outputs/|data/|jobs/)' | head -5)
ENVLEAK=$(git -c core.quotepath=false ls-files 2>/dev/null | grep -E '(^|/)\.env$' | head -3)
if [ -n "$LEAK" ] || [ -n "$ENVLEAK" ]; then
  bad "개인정보 또는 비밀키가 git 에 추적되고 있다:"
  { [ -n "$LEAK" ] && echo "$LEAK"; [ -n "$ENVLEAK" ] && echo "$ENVLEAK"; } | sed 's/^/       /'
  say "조치: git rm --cached <파일> 후 .gitignore 확인"
else
  ok "users/, outputs/, data/, jobs/, .env 모두 미추적"
fi

# ── 6. 작업 트리 상태 ────────────────────────────────────────────────────────
echo "[6/6] 작업 트리"
DELETED=$(git status --porcelain 2>/dev/null | grep -c '^ D' | tr -d ' ')
if [ "${DELETED:-0}" -gt 0 ]; then
  warn "추적 중인 파일 ${DELETED}개가 워킹 트리에서 사라졌다 (과거 소실 사고와 같은 징후)"
  git status --porcelain | grep '^ D' | head -10 | sed 's/^/       /'
  say "의도한 삭제가 아니라면: git restore ."
else
  ok "삭제된 추적 파일 없음"
fi
git status --short | head -15 | sed 's/^/       /'

echo "----------------------------------------------"
if [ "$PROBLEM" -eq 1 ]; then
  echo " ${RED}사람이 확인해야 하는 문제가 있다${RST}"
  exit 1
fi
echo " ${GRN}이상 없음${RST}"
