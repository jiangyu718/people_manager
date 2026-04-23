#!/usr/bin/env python3
"""邮件发送业务逻辑：SMTP 连接、收件人解析、定时任务执行。"""
from datetime import datetime
from html import escape as html_escape
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders as email_encoders
from email.utils import formataddr

from flask import current_app, url_for

from models import db, Personnel, Employee, EmailTemplate, EmailConfig, EmailLog, FormToken
from services.personnel_service import build_prefill_for_employee


SURVEY_MACRO = '{{问卷链接}}'


def _build_form_url(token):
    """构造问卷绝对地址。优先 PUBLIC_BASE_URL；否则尝试 url_for；都没有时回退到相对路径。"""
    base = (current_app.config.get('PUBLIC_BASE_URL') or '').rstrip('/')
    if base:
        return f'{base}/form/{token}'
    try:
        return url_for('public.external_form', token=token, _external=True)
    except RuntimeError:
        server_name = current_app.config.get('SERVER_NAME')
        if server_name:
            scheme = current_app.config.get('PREFERRED_URL_SCHEME', 'http')
            return f'{scheme}://{server_name}/form/{token}'
        current_app.logger.warning(
            '生成问卷链接时没有可用的绝对地址；请配置 PUBLIC_BASE_URL 以便收件人能打开')
        return f'/form/{token}'


def _issue_survey_token(employee):
    prefill = build_prefill_for_employee(employee.employee_id)
    token = str(uuid.uuid4())
    db.session.add(FormToken(token=token, employee_id=employee.employee_id, prefill_data=prefill))
    db.session.commit()
    return token


def _personnel_macros(employee):
    """从员工最新异地记录提取邮件宏替换字典，并返回需附加的附件列表。"""
    p = (Personnel.query
         .filter_by(employee_id=employee.employee_id, status='approved')
         .order_by(Personnel.created_at.desc()).first())
    if not p:
        return {
            '{{人员类型}}': '', '{{职级}}': '', '{{工作所在地}}': '', '{{户口所在地}}': '',
            '{{配偶常住地}}': '', '{{子女常住地}}': '', '{{是否在工作地购置房产}}': '',
            '{{房产交付日期}}': '', '{{在工作地购置房产是否全部售出}}': '',
            '{{过渡期截止}}': '', '{{异地开始时间}}': '', '{{异地结束时间}}': '',
            '{{是否符合异地条件}}': '', '{{备注}}': '',
        }
    def iso(d): return str(d) if d else ''
    fields = {
        '{{人员类型}}': p.personnel_type or '',
        '{{职级}}': str(p.rank) if p.rank else '',
        '{{工作所在地}}': p.work_location or '',
        '{{工作所在地时间}}': iso(p.work_location_date),
        '{{户口所在地}}': p.household_location or '',
        '{{户口所在地时间}}': iso(p.household_location_date),
        '{{配偶常住地}}': p.spouse_location or '',
        '{{配偶常住地时间}}': iso(p.spouse_location_date),
        '{{子女常住地}}': p.children_location or '',
        '{{是否在工作地购置房产}}': p.has_property or '',
        '{{房产交付日期}}': iso(p.property_delivery_date),
        '{{在工作地购置房产是否全部售出}}': p.property_all_sold or '',
        '{{过渡期截止}}': iso(p.transition_end_date),
        '{{异地开始时间}}': iso(p.remote_start_date),
        '{{异地结束时间}}': iso(p.remote_end_date),
        '{{是否符合异地条件}}': '是' if p.is_remote_qualified else '否',
        '{{备注}}': p.notes or '',
    }
    return fields


def render_template_for(tpl, employee):
    macros = {
        '{{姓名}}':   employee.name,
        '{{员工编号}}': employee.employee_id,
        '{{邮箱}}':   employee.email or '',
    }
    subject, body = tpl.subject, tpl.body
    # 问卷链接：每位收件人发一封即生成一次性新 token，从而保证"每封都不一样"
    if SURVEY_MACRO in subject or SURVEY_MACRO in body:
        url = _build_form_url(_issue_survey_token(employee))
        subject = subject.replace(SURVEY_MACRO, url)
        body_link = (f'<a href="{html_escape(url, quote=True)}" '
                     f'target="_blank" rel="noopener">{html_escape(url)}</a>')
        body = body.replace(SURVEY_MACRO, body_link)
    macros.update(_personnel_macros(employee))
    for k, v in macros.items():
        subject = subject.replace(k, v)
        body = body.replace(k, v)
    return subject, body


def _send_one(cfg, subject, body, to_email, cc=None, bcc=None, attachments=None):
    msg = MIMEMultipart('alternative')
    # formataddr + charset='utf-8' 会在显示名含非 ASCII 时按 RFC2047 编码，避免 QQ SMTP 的 550 From 无效
    if cfg.from_name:
        msg['From'] = formataddr((cfg.from_name, cfg.username), charset='utf-8')
    else:
        msg['From'] = cfg.username
    msg['To'] = to_email
    msg['Subject'] = subject
    if cc:
        msg['Cc'] = cc
    msg.attach(MIMEText(body, 'html', 'utf-8'))
    for att in (attachments or []):
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(att.data)
        email_encoders.encode_base64(part)
        # RFC 2231 对非 ASCII 文件名做 utf-8 编码，避免 QQ 邮箱把中文附件名
        # 回退成 tcmime.*.bin。同时附带 Content-Type.name 以提升兼容性。
        part.add_header('Content-Disposition', 'attachment',
                        filename=('utf-8', '', att.filename))
        part.set_param('name', att.filename, header='Content-Type',
                       charset='utf-8', language='')
        msg.attach(part)
    recipients = [to_email]
    if cc:
        recipients += [e.strip() for e in cc.split(',') if e.strip()]
    if bcc:
        recipients += [e.strip() for e in bcc.split(',') if e.strip()]
    if cfg.use_ssl:
        srv = smtplib.SMTP_SSL(cfg.smtp_server, cfg.smtp_port, timeout=15)
    else:
        srv = smtplib.SMTP(cfg.smtp_server, cfg.smtp_port, timeout=15)
        srv.starttls()
    try:
        srv.login(cfg.username, cfg.password)
        srv.sendmail(cfg.username, recipients, msg.as_string())
    finally:
        try:
            srv.quit()
        except Exception:
            pass


def _friendly_smtp_error(exc):
    """把常见 SMTP 错误翻译成中文提示。"""
    msg = str(exc)
    if 'DOMAIN_NOTFOUND_ERR' in msg:
        return ('QQ/企业微信邮箱拒绝发送（DOMAIN_NOTFOUND_ERR）：'
                'SMTP 服务器与发件账号不匹配。'
                '个人 QQ 邮箱请用 smtp.qq.com；企业邮请用 smtp.exmail.qq.com。'
                '并确认密码已填"授权码"而非登录密码。')
    if '535' in msg and ('Auth' in msg or 'auth' in msg):
        return 'SMTP 认证失败：请检查用户名 / 授权码是否正确。'
    if '550' in msg and 'From' in msg:
        return '发件人地址被拒绝：请确保 From 与授权账号一致。'
    return msg


def send_to_employees(tpl, employees, schedule_id=None):
    """向一组 Employee 发送模板邮件，落一条 EmailLog。返回 (success, fail, details)。"""
    cfg = EmailConfig.get_active()
    if not cfg:
        return 0, 0, {'error': 'no_config'}
    success, fail, details = 0, 0, {}
    for emp in employees:
        if not emp.email:
            details[emp.employee_id] = '无邮箱'
            fail += 1
            continue
        try:
            subj, body = render_template_for(tpl, emp)
            _send_one(cfg, subj, body, emp.email,
                      cc=tpl.cc, bcc=tpl.bcc, attachments=list(tpl.attachments))
            details[emp.employee_id] = 'ok'
            success += 1
        except Exception as e:
            details[emp.employee_id] = _friendly_smtp_error(e)
            fail += 1
    db.session.add(EmailLog(
        template_id=tpl.id, template_name=tpl.name,
        recipients=[{'employee_id': e.employee_id} for e in employees],
        subject=tpl.subject, success_count=success,
        fail_count=fail, details=details, schedule_id=schedule_id,
    ))
    db.session.commit()
    return success, fail, details


def resolve_schedule_employees(sched):
    """根据 EmailSchedule 的收件人配置返回 Employee 列表。"""
    mode = sched.recipient_mode
    if mode == 'all':
        return Employee.query.order_by(Employee.employee_id).all()
    if mode == 'ids':
        ids = sched.recipient_ids or []
        if not ids:
            return []
        return Employee.query.filter(Employee.employee_id.in_(ids)).all()
    if mode == 'filter':
        f = sched.recipient_filter or {}
        q = Personnel.query.filter_by(status='approved')
        if f.get('rank_min'):
            q = q.filter(Personnel.rank >= int(f['rank_min']))
        if f.get('rank_max'):
            q = q.filter(Personnel.rank <= int(f['rank_max']))
        if f.get('remote_from'):
            q = q.filter(Personnel.remote_start_date >= datetime.fromisoformat(f['remote_from']).date())
        if f.get('remote_to'):
            q = q.filter(Personnel.remote_start_date <= datetime.fromisoformat(f['remote_to']).date())
        want_remote = f.get('remote')
        emp_ids = set()
        for p in q.all():
            if want_remote == 'yes' and not p.is_remote_qualified:
                continue
            if want_remote == 'no' and p.is_remote_qualified:
                continue
            emp_ids.add(p.employee_id)
        if not emp_ids:
            return []
        return Employee.query.filter(Employee.employee_id.in_(list(emp_ids))).all()
    return []


def run_email_schedule(sched):
    tpl = EmailTemplate.query.get(sched.template_id)
    if not tpl:
        return
    emps = resolve_schedule_employees(sched)
    send_to_employees(tpl, emps, schedule_id=sched.id)


def employee_personnel_snapshot(employees):
    """为员工列表附加最新 approved Personnel 概要，用于前端筛选。"""
    info = {}
    for emp in employees:
        p = (Personnel.query
             .filter_by(employee_id=emp.employee_id, status='approved')
             .order_by(Personnel.created_at.desc()).first())
        info[emp.employee_id] = {
            'rank': p.rank if p else '',
            'remote': 'yes' if (p and p.is_remote_qualified) else ('no' if p else ''),
            'remote_start': p.remote_start_date.isoformat() if (p and p.remote_start_date) else '',
        }
    return info
