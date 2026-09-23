"""라우트 모음. 기능을 추가할 때는 이 폴더에 Blueprint 파일을 만들고 여기에 등록한다.

예정: parse(S1-2), generate(S1-3), elicit(S3), jobs(S4), matches(S5), applications(S9), auth(S10)
"""


def register_blueprints(app):
    from web.routes import documents, elicit, generate, jobs, main, parse, profile

    for module in (main, profile, documents, parse, generate, elicit, jobs):
        app.register_blueprint(module.bp)
