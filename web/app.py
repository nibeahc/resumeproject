"""진입점. 앱 조립은 web/__init__.py 의 create_app() 이 한다."""
import sys
from pathlib import Path

# python3 web/app.py 로 실행하면 sys.path 에 web/ 만 들어가므로, 프로젝트 루트를 넣어 web, lib 를 import 할 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402

app = create_app()

if __name__ == '__main__':
    # 코드·템플릿이 바뀌면 서버가 알아서 다시 읽는다 (사용자 요청, 2026-10-12).
    # debug=True 는 쓰지 않는다 — 브라우저에서 코드를 실행할 수 있는 디버거가 같이 켜진다 (S0-2).
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(debug=False, use_reloader=True, port=8080)
