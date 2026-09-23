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
    return app
