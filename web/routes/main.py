"""메인 화면: 사용자 목록, 사용자 상세, 신규 사용자, 사용자 삭제."""
import shutil
from datetime import date, datetime

from flask import Blueprint, current_app, jsonify, render_template, request

from lib import applications, db, trends
from web.data import get_experiences, get_outputs, get_raw_files, get_users
from web.paths import output_dir, project_root, trash_dir, trends_path, user_dir, valid_segment

bp = Blueprint('main', __name__)


def _db_path():
    """S9-2(대시보드)부터 사용자 상세 화면도 SQLite(applications)를 본다. jobs.py 등과 같은
    이유로 DB_PATH 설정을 존중한다."""
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path


@bp.route('/')
def index():
    return render_template('index.html', users=get_users())


_TRENDS_BANNER_DAYS = 7  # 이 안에 갱신됐으면 대시보드에 "업데이트됨" 배너를 띄운다


@bp.route('/user/<name>')
def user_detail(name):
    user_dir(name)

    # 고도화(2026-09-27): 페이지를 열 때마다 트렌드 문서가 오래됐는지 확인하고, 오래됐으면
    # 페이지 로딩을 막지 않고 백그라운드에서 조용히 다시 조사한다("앱 자체가 자동으로 주기적
    # 으로 검색"). 이번 요청에서 바로 끝나지 않으므로, 배너는 "이전에 이미 갱신되어 있던" 결과
    # 기준으로 보여준다 — 방금 시작된 갱신은 다음 페이지 로드부터 반영된다.
    # TESTING 모드에서는 절대 트리거하지 않는다 — 안 그러면 이 라우트를 부르는 모든 테스트가
    # 백그라운드 스레드에서 실제 web_search API 를 호출하게 된다(과거 스트리밍 응답 patch
    # 누락 사고와 같은 종류의 함정).
    if not current_app.config.get('TESTING'):
        trends.trigger_background_refresh(trends_path())
    trends_updated = trends.last_updated(trends_path())
    trends_recent = bool(trends_updated and (date.today() - trends_updated).days <= _TRENDS_BANNER_DAYS)

    return render_template(
        'user.html',
        user=name,
        experiences=get_experiences(name),
        outputs=get_outputs(name),
        raw=get_raw_files(name),
        dashboard=applications.dashboard_summary(name, db_path=_db_path()),
        trends_recent=trends_recent,
        trends_updated=trends_updated,
    )


@bp.route('/new-user', methods=['POST'])
def new_user():
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify(success=False, error='이름을 입력해주세요.')
    if not valid_segment(name):
        return jsonify(success=False, error='이름에 사용할 수 없는 문자가 있습니다. (/ \\ .. 등)')
    for sub in ('raw/apply', 'raw/logs', 'experiences'):
        (user_dir(name) / sub).mkdir(parents=True, exist_ok=True)
    output_dir(name).mkdir(parents=True, exist_ok=True)
    skeleton = project_root() / 'templates' / 'user-skeleton' / 'profile.md'
    profile_dest = user_dir(name) / 'profile.md'
    if skeleton.exists() and not profile_dest.exists():
        profile_dest.write_text(
            skeleton.read_text(encoding='utf-8').replace('[이름]', name),
            encoding='utf-8'
        )
    return jsonify(success=True, name=name, redirect=f'/profile/{name}/setup')


@bp.route('/delete-user/<name>', methods=['POST'])
def delete_user(name):
    """홈 화면에서 사용자를 삭제한다(2026-09-27, 사용자 요청). 실제로는 rm 이 아니라
    project_root()/_trash/ 로 옮긴다 — 화면상으로는 목록에서 즉시 사라져 "삭제됐다"와
    똑같이 보이지만, 확인 창을 잘못 눌렀을 때를 대비해 며칠은 파일이 남아있게 하기 위함
    (git 추적 대상은 아니므로 커밋에는 영향 없음). DB 쪽 matches/applications/documents
    행도 같이 지운다 — 안 그러면 같은 이름을 다시 만들었을 때 예전 이력이 되살아난다.
    """
    udir = user_dir(name)
    if not udir.exists():
        return jsonify(success=False, error='존재하지 않는 사용자입니다.'), 404

    ts = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = trash_dir() / f'{name}__{ts}'
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(udir), str(dest / 'users'))
    odir = output_dir(name)
    if odir.exists():
        shutil.move(str(odir), str(dest / 'outputs'))

    db.delete_user_data(name, db_path=_db_path())
    return jsonify(success=True)
