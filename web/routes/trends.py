"""자소서 작성 트렌드 검토 화면 (고도화 2026-09-27, S11-6 에서 승인 절차 추가).

예전엔 자동 수집 결과를 그대로 보여주기만 했고, 그 파일이 곧바로 생성 프롬프트에 들어갔다.
한 달에 한 번 자동으로 덮어써졌으므로 아무도 읽지 않은 검색 결과가 다음 달부터 작성 정책이
됐다(설계검토 F-1). 이제 수집본(raw/)과 승인본(approved.md)을 나누고, **승인본만** 생성에
쓴다. 이 화면이 그 둘을 나란히 보여주고 승인을 받는다.

승인은 쓰기 동작이지만 외부 API 를 부르지 않는다 — 이미 수집해둔 파일을 옮길 뿐이다.
"""
from flask import Blueprint, jsonify, render_template, request

from lib import trends
from web.paths import trends_dir

bp = Blueprint('trends', __name__)


@bp.route('/trends')
def trends_page():
    base = trends_dir()
    return render_template(
        'trends.html',
        content=trends.load_trends(base),            # 승인본 — 실제로 생성에 쓰이는 것
        raw_content=trends.load_raw(base),           # 최신 수집본 — 아직 안 쓰임
        approved_at=trends.last_approved(base),
        collected_at=trends.last_updated(base),
        pending=trends.has_pending_review(base),
    )


@bp.route('/trends/approve', methods=['POST'])
def approve():
    """검토한 내용을 승인본으로 올린다. 본문(content)을 주면 그대로, 안 주면 최신 수집본 전체를
    쓴다 — 항목을 추리거나 문구를 고쳤으면 고친 결과를 보내면 된다."""
    data = request.get_json(silent=True) or request.form
    content = (data.get('content') or '').strip() or None
    saved = trends.approve(trends_dir(), content)
    if saved is None:
        return jsonify(success=False, error='승인할 내용이 없습니다.'), 400
    return jsonify(success=True)
