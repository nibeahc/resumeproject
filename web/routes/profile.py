"""프로필 설정 화면."""
from flask import Blueprint, redirect, render_template, request, url_for

from web.data import parse_profile, parse_writing_rules
from web.paths import user_dir

bp = Blueprint('profile', __name__)


@bp.route('/profile/<name>/setup', methods=['GET'])
def profile_setup(name):
    if not user_dir(name).exists():
        return redirect(url_for('main.index'))
    return render_template('setup.html', user=name, profile=parse_profile(name),
                            writing_rules=parse_writing_rules(name))


@bp.route('/profile/<name>/setup', methods=['POST'])
def profile_setup_save(name):
    f = request.form
    gpa = f.get('gpa', '').strip()
    gpa_max = f.get('gpa_max', '4.5').strip()
    content = f"""# {name} 프로필

## 기본 정보
- 이름: {f.get('이름', name).strip()}
- 학교/소속: {f.get('학교', '').strip()}
- 학번/사번: {f.get('학번', '').strip()}
- 연락처: {f.get('연락처', '').strip()}
- 이메일: {f.get('이메일', '').strip()}
- 생년월일: {f.get('생년월일', '').strip()}

## 어학 / 자격
{f.get('어학자격', '').strip()}

## 학점
- GPA: {gpa}/{gpa_max}

## 목표 직무
{f.get('목표직무', '').strip()}

## 핵심 강점

## 가치관 / 성격
"""
    (user_dir(name) / 'profile.md').write_text(content, encoding='utf-8')

    rules_content = f"""# {name} 작성 규칙

> 사용자가 직접 설정한 규칙. writing-style.md 와 동일 가중치로 적용됨.

## 구조

{f.get('구조', '').strip()}

## 금지 표현

{f.get('금지표현', '').strip()}

## 선호 표현

{f.get('선호표현', '').strip()}

## 기타

{f.get('기타', '').strip()}
"""
    (user_dir(name) / 'writing-rules.md').write_text(rules_content, encoding='utf-8')

    return redirect(url_for('main.user_detail', name=name))
