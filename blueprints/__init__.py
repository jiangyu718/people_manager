from blueprints.public import public_bp
from blueprints.personnel import personnel_bp
from blueprints.employee import employee_bp
from blueprints.email import email_bp
from blueprints.auth import auth_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(personnel_bp)
    app.register_blueprint(employee_bp)
    app.register_blueprint(email_bp)
