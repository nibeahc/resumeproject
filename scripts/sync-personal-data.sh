#!/bin/bash
# sync-personal-data.sh — private 작업본의 개인정보 파일을 공개용 폴더의 작업 트리로 복사한다.
#
# 왜: 대시보드(web/app.py)를 공개용 폴더에서 띄우면 그 폴더의 outputs/·users/ 를 읽는다.
# private 에서 고친 자소서·경험 DB 가 거기 반영되지 않아 대시보드에 옛 내용이 보였다
# (2026-10-12, 디지프렌드로지스틱스 최종본의 "지난해").
#
# 공개 레포의 .gitignore 가 users/·outputs/·companies/ 를 막으므로 복사만 되고 커밋되지 않는다.
# .githooks/post-commit, post-merge 가 자동으로 부른다. 수동 실행: bash scripts/sync-personal-data.sh
#
# - 단방향(private → 공개용 폴더)이다. 반대 방향은 없다
# - --update: 공개용 폴더 쪽 파일이 더 새로우면(대시보드에서 직접 고친 경우) 덮어쓰지 않고 알린다
# - --delete 는 쓰지 않는다. private 에서 지운 파일은 공개용 폴더에서 손으로 지운다

set -uo pipefail
cd "$(dirname "$0")/.." || exit 0

PUBLIC_REPO="/Users/chaebin/Documents/DIVE/Claude CLI/ResumeProject"

# export-public.sh 가 이 스크립트와 hook 을 공개 레포에도 복사한다. 거기서는 아무것도 하지 않는다
[ "$(pwd -P)" = "$(cd "$PUBLIC_REPO" 2>/dev/null && pwd -P)" ] && exit 0
[ -d "$PUBLIC_REPO/.git" ] || { echo "[sync] 공개용 폴더 없음, 건너뜀: $PUBLIC_REPO"; exit 0; }

# 공개 레포가 개인정보를 무시하는지 먼저 확인한다. 아니면 복사하지 않는다 (실수로 커밋되는 것 방지)
for probe in users/x/x.md outputs/x/x.md companies/x.md; do
  if ! git -C "$PUBLIC_REPO" check-ignore -q "$probe"; then
    echo "[sync] 중단: 공개 레포가 ${probe%%/*}/ 를 무시하지 않는다. .gitignore 를 확인할 것"
    exit 0
  fi
done

for d in users outputs companies; do
  [ -d "$d" ] || continue
  rsync -a --update --exclude='.DS_Store' "$d/" "$PUBLIC_REPO/$d/"
  # --update 로 건너뛴 파일 = 공개용 폴더 쪽이 더 새롭고 내용이 다른 파일
  newer=$(rsync -anc --exclude='.DS_Store' --out-format='%n' "$d/" "$PUBLIC_REPO/$d/" | grep -v '/$' || true)
  if [ -n "$newer" ]; then
    echo "[sync] 공개용 폴더 쪽이 더 새로워서 덮어쓰지 않은 파일 ($d/):"
    echo "$newer" | sed 's/^/         /'
    echo "         대시보드에서 고친 내용이면 private 로 옮긴 뒤 다시 실행할 것"
  fi
done
echo "[sync] 개인정보 파일 → 공개용 폴더 동기화 완료"
