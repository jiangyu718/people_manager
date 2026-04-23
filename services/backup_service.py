#!/usr/bin/env python3
"""数据备份：构建人员异地情况 Excel + 拷贝 SQLite 数据库，通过邮件发送。"""
import io
import os
import sqlite3
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
from flask import current_app

from models import db, Personnel, EmailConfig, EmailLog, beijing_now
from services.email_service import _send_one, _friendly_smtp_error


def build_personnel_excel():
    """导出全部 approved 人员 -> Excel 字节流。"""
    personnels = (Personnel.query
                  .filter_by(status='approved')
                  .order_by(Personnel.created_at.desc()).all())
    rows = [{
        '人员类型': p.personnel_type,
        '员工编号': p.employee_id,
        '姓名': p.name,
        '职级': p.rank,
        '工作所在地': p.work_location,
        '工作所在地时间': str(p.work_location_date) if p.work_location_date else '',
        '户口所在地': p.household_location,
        '户口所在地时间': str(p.household_location_date) if p.household_location_date else '',
        '配偶常住地': p.spouse_location or '',
        '配偶常住地时间': str(p.spouse_location_date) if p.spouse_location_date else '',
        '子女常住地': p.children_location or '',
        '是否在工作地购置房产': p.has_property,
        '房产交付日期': str(p.property_delivery_date) if p.property_delivery_date else '',
        '在工作地购置房产是否全部售出': p.property_all_sold or '',
        '过渡期截止': str(p.transition_end_date) if p.transition_end_date else '',
        '异地开始时间': str(p.remote_start_date) if p.remote_start_date else '',
        '异地结束时间': str(p.remote_end_date) if p.remote_end_date else '',
        '是否符合异地条件': '是' if p.is_remote_qualified else '否',
        '备注': p.notes or '',
    } for p in personnels]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _resolve_sqlite_path():
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if not uri.startswith('sqlite:'):
        return None
    # sqlite:///relative -> instance 相对；sqlite:////abs -> 绝对
    path = uri[len('sqlite:///'):]
    if path.startswith('/'):
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(current_app.instance_path, path)


def build_sqlite_backup():
    """使用 SQLite Online Backup API 安全拷贝数据库 -> 字节流。"""
    src_path = _resolve_sqlite_path()
    if not src_path or not os.path.exists(src_path):
        raise FileNotFoundError(f'未找到数据库文件：{src_path}')
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    try:
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(tmp.name)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        with open(tmp.name, 'rb') as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _parse_recipients(raw):
    if not raw:
        return []
    seps = [',', '，', ';', '；', '\n', ' ']
    s = raw
    for sep in seps:
        s = s.replace(sep, ',')
    return [x.strip() for x in s.split(',') if x.strip()]


def run_backup(cfg):
    """执行一次备份并发送邮件。成功返回 (ok=True, info)，失败抛异常。"""
    mail_cfg = EmailConfig.get_active()
    if not mail_cfg:
        raise RuntimeError('尚未配置邮件发送账号')
    recipients = _parse_recipients(cfg.recipients)
    if not recipients:
        raise RuntimeError('尚未配置备份收件人')

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    excel_bytes = build_personnel_excel()
    db_bytes = build_sqlite_backup()
    attachments = [
        SimpleNamespace(
            filename=f'异地情况记录_{ts}.xlsx',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            data=excel_bytes,
        ),
        SimpleNamespace(
            filename=f'personnel_{ts}.db',
            content_type='application/octet-stream',
            data=db_bytes,
        ),
    ]

    subject = cfg.subject or f'人员管理系统数据备份 {ts}'
    body = (f'<p>这是人员管理系统自动生成的数据备份。</p>'
            f'<p>备份时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>'
            f'<ul><li>异地情况记录 Excel</li><li>原始数据库 personnel.db</li></ul>')

    details = {}
    success, fail = 0, 0
    for to_addr in recipients:
        try:
            _send_one(mail_cfg, subject, body, to_addr, attachments=attachments)
            details[to_addr] = 'ok'
            success += 1
        except Exception as e:
            details[to_addr] = _friendly_smtp_error(e)
            fail += 1

    db.session.add(EmailLog(
        template_id=None, template_name='[数据备份]',
        recipients=[{'email': r} for r in recipients],
        subject=subject, success_count=success,
        fail_count=fail, details=details, schedule_id=None,
    ))
    cfg.last_run_at = beijing_now()
    cfg.last_status = 'ok' if fail == 0 else ('fail' if success == 0 else 'partial')
    cfg.last_error = None if fail == 0 else '; '.join(f'{k}: {v}' for k, v in details.items() if v != 'ok')
    db.session.commit()
    if success == 0:
        raise RuntimeError(cfg.last_error or '发送失败')
    return success, fail, details
