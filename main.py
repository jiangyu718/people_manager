#!/usr/bin/env python3
from flask import Flask, session
from flask_wtf.csrf import CSRFProtect

from config import Config
from models import db
from blueprints import register_blueprints
from blueprints.auth import require_login
from scheduler import init_scheduler
from utils import CHINA_CITIES





def create_app(config_cls=Config):
    app = Flask(__name__)
    app.config.from_object(config_cls)

    db.init_app(app)
    CSRFProtect(app)
    register_blueprints(app)
    app.before_request(require_login)

    @app.context_processor
    def inject_globals():
        return {'china_cities': CHINA_CITIES, 'current_user': session.get('user')}

    with app.app_context():
        db.create_all()
        init_scheduler(app)

    return app


app = create_app()


if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5495)