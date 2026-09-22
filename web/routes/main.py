"""메인 화면: 사용자 목록, 사용자 상세, 신규 사용자."""
from flask import Blueprint, jsonify, render_template, request

from web.data import get_experiences, get_outputs, get_raw_files, get_users
from web.paths import output_dir, project_root, user_dir, valid_segment

bp = Blueprint('main', __name__)


@bp.route('/')
def index():
    return render_template('index.html', users=get_users())


@bp.route('/user/<name>')
def user_detail(name):
    user_dir(name)
    return render_template(
        'user.html',
        user=name,
        experiences=get_experiences(name),
        outputs=get_outputs(name),
        raw=get_raw_files(name),
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
