#!/usr/bin/env python3
import json
from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import types


BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now():
    """当前北京时间（naive datetime，方便与旧字段兼容）。"""
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


class JSONType(types.TypeDecorator):
    impl = types.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    def process_result_value(self, value, dialect):
        return json.loads(value) if value else None


db = SQLAlchemy()


class Employee(db.Model):
    __tablename__ = 'employee'
    employee_id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=beijing_now)


class Personnel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    personnel_type = db.Column(db.String(50), nullable=False)
    employee_id = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    rank = db.Column(db.Integer, nullable=True)
    rank_date = db.Column(db.Date, nullable=True)
    work_location = db.Column(db.String(100), nullable=False)
    household_location = db.Column(db.String(100), nullable=False)
    spouse_location = db.Column(db.String(100), nullable=True)
    children_location = db.Column(db.String(100), nullable=True)
    has_property = db.Column(db.String(10), nullable=False)
    property_delivery_date = db.Column(db.Date, nullable=True)
    attachments = db.relationship('Attachment', backref='personnel', lazy=True,
                                  foreign_keys='Attachment.personnel_id')
    transition_end_date = db.Column(db.Date, nullable=True)
    remote_start_date = db.Column(db.Date, nullable=True)
    remote_end_date = db.Column(db.Date, nullable=True)
    work_location_date = db.Column(db.Date, nullable=True)
    household_location_date = db.Column(db.Date, nullable=True)
    spouse_location_date = db.Column(db.Date, nullable=True)
    property_all_sold = db.Column(db.String(10), nullable=True)   # 在工作地购置房产是否全部售出
    notes = db.Column(db.Text, nullable=True)                      # 备注（管理员填写）
    is_no_change = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='pending')  # pending/approved/rejected/deleted
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @property
    def is_remote_qualified(self):
        cond1 = (bool(self.work_location) and bool(self.household_location)
                 and self.work_location != self.household_location)
        cond2 = (not self.spouse_location) or (self.spouse_location != self.work_location)
        return cond1 and cond2


class PersonnelHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    personnel_id = db.Column(db.Integer, nullable=True)
    history_type = db.Column(db.String(20), nullable=False)
    data = db.Column(JSONType, nullable=False)
    should_import = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=beijing_now)


class Trash(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    personnel_id = db.Column(db.Integer, nullable=False)
    data = db.Column(JSONType, nullable=False)
    deleted_at = db.Column(db.DateTime, default=beijing_now)


class Attachment(db.Model):
    __tablename__ = 'attachment'
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    personnel_id = db.Column(db.Integer, db.ForeignKey('personnel.id'), nullable=True)
    email_template_id = db.Column(db.Integer, db.ForeignKey('email_template.id'), nullable=True)
    category = db.Column(db.String(20), nullable=True)  # 'property' | 'household' | None (email)
    created_at = db.Column(db.DateTime, default=beijing_now)


class FormToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(100), unique=True, nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    employee_id = db.Column(db.String(20), nullable=True)
    prefill_data = db.Column(JSONType, nullable=True)


class EmailTemplate(db.Model):
    __tablename__ = 'email_template'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    cc = db.Column(db.String(500), nullable=True)
    bcc = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)
    attachments = db.relationship('Attachment', backref='email_template', lazy=True,
                                  foreign_keys='Attachment.email_template_id')


class EmailConfig(db.Model):
    __tablename__ = 'email_config'
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(20), nullable=False, default='custom')  # qq / cmss / custom
    smtp_server = db.Column(db.String(100), nullable=False)
    smtp_port = db.Column(db.Integer, nullable=False, default=465)
    use_ssl = db.Column(db.Boolean, default=True)
    username = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    from_name = db.Column(db.String(50), nullable=True)
    # 每个 provider 保留一份配置；is_active 标记当前生效的那份
    is_active = db.Column(db.Boolean, nullable=False, default=False)

    @classmethod
    def get_active(cls):
        """返回当前生效的配置（is_active=True）。若没有任何激活行，回退到任意一行。"""
        return (cls.query.filter_by(is_active=True).first()
                or cls.query.first())


class EmailLog(db.Model):
    __tablename__ = 'email_log'
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, nullable=True)
    template_name = db.Column(db.String(100), nullable=True)
    recipients = db.Column(JSONType, nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    sent_at = db.Column(db.DateTime, default=beijing_now)
    success_count = db.Column(db.Integer, default=0)
    fail_count = db.Column(db.Integer, default=0)
    details = db.Column(JSONType, nullable=True)
    schedule_id = db.Column(db.Integer, nullable=True)


class BackupConfig(db.Model):
    """数据备份配置（单例）。定时将人员 Excel + personnel.db 发送到配置邮箱。"""
    __tablename__ = 'backup_config'
    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=False)
    # daily / weekly / monthly
    schedule_type = db.Column(db.String(20), nullable=False, default='daily')
    run_time = db.Column(db.String(5), nullable=False, default='02:00')  # HH:MM
    day_of_week = db.Column(db.Integer, nullable=True)   # 0-6 周一至周日（weekly）
    day_of_month = db.Column(db.Integer, nullable=True)  # 1-31（monthly）
    recipients = db.Column(db.String(500), nullable=False, default='')  # 逗号分隔
    subject = db.Column(db.String(200), nullable=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    last_status = db.Column(db.String(20), nullable=True)  # ok / fail
    last_error = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)


class AppCredential(db.Model):
    """系统账户（单行，首次启动时通过初始化页面设置）。"""
    __tablename__ = 'app_credential'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(50), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)


class EmailSchedule(db.Model):
    __tablename__ = 'email_schedule'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    template_id = db.Column(db.Integer, nullable=False)
    # daily / monthly / once
    schedule_type = db.Column(db.String(20), nullable=False)
    run_time = db.Column(db.String(5), nullable=False, default='09:00')   # HH:MM
    day_of_month = db.Column(db.Integer, nullable=True)   # for monthly
    run_date = db.Column(db.Date, nullable=True)          # for once
    # recipient selection: 'all' | 'ids' | 'filter'
    recipient_mode = db.Column(db.String(20), nullable=False, default='ids')
    recipient_ids = db.Column(JSONType, nullable=True)    # list of employee_id
    recipient_filter = db.Column(JSONType, nullable=True) # {'rank_min':..,'rank_max':..,'remote':'yes|no|any','remote_from':..,'remote_to':..}
    enabled = db.Column(db.Boolean, default=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)
