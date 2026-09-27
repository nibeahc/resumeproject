"""자소서 작성 트렌드 조회 화면 (고도화, 2026-09-27). lib/trends.py 가 백그라운드에서
갱신해둔 문서를 그대로 보여준다 — 이 라우트 자체는 API 를 호출하지 않는다(읽기 전용).
"""
from flask import Blueprint, render_template

from lib import trends
from web.paths import trends_path

bp = Blueprint('trends', __name__)


@bp.route('/trends')
def trends_page():
    content = trends.load_trends(trends_path())
    return render_template('trends.html', content=content)
