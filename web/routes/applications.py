"""지원 상태와 이력 화면 (ROADMAP S9-1). lib/applications.py 를 그대로 쓴다.
"""
from flask import Blueprint, current_app, jsonify, render_template, request

from lib import applications, db

bp = Blueprint('applications', __name__)


def _db_path():
    """S9 도 SQLite(applications/application_events)를 쓴다. jobs.py/matches.py 와 같은
    이유로 DB_PATH 설정을 존중한다."""
    path = current_app.config.get('DB_PATH')
    db.init_db(path)
    return path


@bp.route('/applications/<name>')
def applications_page(name):
    apps = applications.list_applications(name, db_path=_db_path())
    available = applications.list_available_jobs(name, db_path=_db_path())
    return render_template('applications.html', user=name, apps=apps, available=available,
                           statuses=applications.VALID_STATUSES)


@bp.route('/applications/<name>/<int:job_id>/status', methods=['POST'])
def update_status(name, job_id):
    data = request.get_json(silent=True) or {}
    status = (data.get('status') or '').strip()
    note = (data.get('note') or '').strip()
    result = applications.set_status(name, job_id, status, note=note, db_path=_db_path())
    if not result.ok:
        return jsonify(success=False, error=result.error), 400
    return jsonify(success=True, status=result.status, changed=result.changed)


@bp.route('/applications/<name>/<int:job_id>/history')
def application_history(name, job_id):
    history = applications.get_history(name, job_id, db_path=_db_path())
    return jsonify(history=[
        {'from_status': h.from_status, 'to_status': h.to_status, 'note': h.note, 'created_at': h.created_at}
        for h in history
    ])
