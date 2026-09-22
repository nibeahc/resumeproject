"""진입점. 앱 조립은 web/__init__.py 의 create_app() 이 한다."""
import sys
from pathlib import Path

# python3 web/app.py 로 실행하면 sys.path 에 web/ 만 들어가므로, 프로젝트 루트를 넣어 web, lib 를 import 할 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from web import create_app  # noqa: E402

app = create_app()

if __name__ == '__main__':
    app.run(debug=False, port=8080)
