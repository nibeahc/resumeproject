#!/bin/bash
cd "$(dirname "$0")"

# flask 설치 확인
if ! python3 -c "import flask" 2>/dev/null; then
  echo "Flask 설치 중..."
  pip3 install -r requirements.txt
fi

echo "서버 시작: http://localhost:8080"
python3 app.py
