"""웹 UI 패키지. create_app() 으로 앱을 만든다.

    python3 web/app.py           # 개발 서버 (run.sh 도 이 방식)
    flask --app web run          # 프로젝트 루트에서
"""
from pathlib import Path

from flask import Flask

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(config=None):
    """config 로 PROJECT_ROOT 등을 덮어쓸 수 있다 (테스트가 임시 디렉터리를 주입한다)."""
    app = Flask(__name__)
    app.config['PROJECT_ROOT'] = DEFAULT_PROJECT_ROOT
    app.config['DB_PATH'] = None  # None 이면 lib.config.db_path() (.env DB_PATH) 를 그대로 쓴다.
                                   # 테스트가 jobs 테이블처럼 PROJECT_ROOT 와 무관한 전역 DB 를
                                   # 건드리는 라우트를 검증할 때 임시 DB 경로를 주입한다.
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
    if config:
        app.config.update(config)

    from web.routes import register_blueprints
    register_blueprints(app)

    # S11-6: 예전의 단일 파일(references/trends/coverletter-trends.md)을 raw/ 로 옮긴다.
    # 승인본으로 올리지는 않는다 — 검토를 거치지 않은 수집 결과이기 때문이다.
    # 여러 번 불러도 안전하고, 옮길 게 없으면 아무 일도 하지 않는다.
    from lib import trends as _trends
    try:
        _trends.migrate_legacy(app.config['PROJECT_ROOT'] / 'references' / 'trends')
    except OSError:
        pass  # 마이그레이션 실패로 앱 기동을 막지 않는다

    return app
