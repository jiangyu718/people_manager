#!/usr/bin/env python3
"""邮件模块：模板、配置、发送、定时任务、发送记录。"""
from datetime import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, current_app)

from models import (db, Attachment, Employee, EmailTemplate, EmailConfig,
                    EmailLog, EmailSchedule, BackupConfig, beijing_now)
from services.email_service import (send_to_employees, run_email_schedule,
                                     employee_personnel_snapshot)
from services.backup_service import run_backup
import scheduler as scheduler_module


email_bp = Blueprint('email', __name__, url_prefix='/email')


# ── 模板 ───────────────────────────────────────────────────

@email_bp.route('/templates')
def templates():
    tpls = EmailTemplate.query.order_by(EmailTemplate.updated_at.desc()).all()
    return render_template('email_list.html', templates=tpls)


@email_bp.route('/templates/new', methods=['GET', 'POST'])
def template_new():
    macros = current_app.config['EMAIL_MACROS']
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        subject = request.form.get('subject', '').strip()
        body = request.form.get('body', '').strip()
        if not name or not subject or not body:
            flash('模板名称、主题、正文为必填', 'error')
            return redirect(url_for('email.template_new'))
        tpl = EmailTemplate(
            name=name, subject=subject, body=body,
            cc=request.form.get('cc', '').strip() or None,
            bcc=request.form.get('bcc', '').strip() or None,
        )
        db.session.add(tpl)
        db.session.flush()
        for f in request.files.getlist('attachments'):
            if f and f.filename:
                db.session.add(Attachment(
                    filename=f.filename, content_type=f.content_type,
                    data=f.read(), email_template_id=tpl.id,
                ))
        db.session.commit()
        flash('模板已创建', 'success')
        return redirect(url_for('email.templates'))
    return render_template('email_edit.html', tpl=None, macros=macros)


@email_bp.route('/templates/<int:id>/edit', methods=['GET', 'POST'])
def template_edit(id):
    tpl = EmailTemplate.query.get_or_404(id)
    macros = current_app.config['EMAIL_MACROS']
    if request.method == 'POST':
        tpl.name = request.form.get('name', '').strip()
        tpl.subject = request.form.get('subject', '').strip()
        tpl.body = request.form.get('body', '').strip()
        tpl.cc = request.form.get('cc', '').strip() or None
        tpl.bcc = request.form.get('bcc', '').strip() or None
        if not tpl.name or not tpl.subject or not tpl.body:
            flash('模板名称、主题、正文为必填', 'error')
            return redirect(url_for('email.template_edit', id=id))
        for f in request.files.getlist('attachments'):
            if f and f.filename:
                db.session.add(Attachment(
                    filename=f.filename, content_type=f.content_type,
                    data=f.read(), email_template_id=tpl.id,
                ))
        db.session.commit()
        flash('模板已更新', 'success')
        return redirect(url_for('email.templates'))
    return render_template('email_edit.html', tpl=tpl, macros=macros)


@email_bp.route('/templates/<int:id>/delete', methods=['POST'])
def template_delete(id):
    tpl = EmailTemplate.query.get_or_404(id)
    db.session.delete(tpl)
    db.session.commit()
    flash('模板已删除', 'success')
    return redirect(url_for('email.templates'))


@email_bp.route('/attachments/<int:att_id>/delete', methods=['POST'])
def attachment_delete(att_id):
    att = Attachment.query.get_or_404(att_id)
    tpl_id = att.email_template_id
    db.session.delete(att)
    db.session.commit()
    return redirect(url_for('email.template_edit', id=tpl_id))


# ── 邮件配置 ───────────────────────────────────────────────

@email_bp.route('/config', methods=['GET', 'POST'])
def config():
    presets = current_app.config['PROVIDER_PRESETS']
    if request.method == 'POST':
        provider = request.form.get('provider', 'custom')
        smtp_server = request.form.get('smtp_server', '').strip()
        smtp_port = int(request.form.get('smtp_port', 465) or 465)
        use_ssl = request.form.get('use_ssl') == '1'
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        from_name = request.form.get('from_name', '').strip()
        if not smtp_server or not username or not password:
            flash('SMTP服务器、用户名、密码为必填', 'error')
            return redirect(url_for('email.config'))
        # 按 provider upsert：每个服务商只保留一行
        cfg = EmailConfig.query.filter_by(provider=provider).first()
        if cfg is None:
            cfg = EmailConfig(provider=provider)
            db.session.add(cfg)
        cfg.smtp_server = smtp_server
        cfg.smtp_port = smtp_port
        cfg.use_ssl = use_ssl
        cfg.username = username
        cfg.password = password
        cfg.from_name = from_name or None
        # 激活当前 provider，停用其他
        EmailConfig.query.filter(EmailConfig.provider != provider).update(
            {'is_active': False}, synchronize_session=False)
        cfg.is_active = True
        db.session.commit()
        flash('邮件配置已保存', 'success')
        return redirect(url_for('email.config'))
    active_cfg = EmailConfig.get_active()
    saved_configs = {c.provider: {
        'smtp_server': c.smtp_server,
        'smtp_port': c.smtp_port,
        'use_ssl': bool(c.use_ssl),
        'username': c.username,
        'password': c.password,
        'from_name': c.from_name or '',
    } for c in EmailConfig.query.all()}
    return render_template('email_config.html', cfg=active_cfg,
                           presets=presets, saved_configs=saved_configs)


# ── 发送 ───────────────────────────────────────────────────

@email_bp.route('/send', methods=['GET', 'POST'])
def send():
    if request.method == 'POST':
        employee_ids = request.form.getlist('employee_ids')
        template_id = request.form.get('template_id')
        if not employee_ids:
            flash('请选择至少一位员工', 'warning')
            return redirect(url_for('email.send'))
        if not EmailConfig.get_active():
            flash('请先配置邮件发送账号', 'error')
            return redirect(url_for('email.config'))
        tpl = EmailTemplate.query.get(template_id)
        if not tpl:
            flash('请选择邮件模板', 'warning')
            return redirect(url_for('email.send'))
        emps = Employee.query.filter(Employee.employee_id.in_(employee_ids)).all()
        success, fail, details = send_to_employees(tpl, emps)
        if fail == 0:
            flash(f'已成功发送 {success} 封邮件', 'success')
        else:
            emp_name = {e.employee_id: e.name for e in emps}
            err_groups = {}
            for eid, msg in details.items():
                if msg == 'ok':
                    continue
                err_groups.setdefault(msg, []).append(emp_name.get(eid, eid))
            lines = [f'发送完成：成功 {success} 封，失败 {fail} 封。']
            for err, names in list(err_groups.items())[:5]:
                preview = '、'.join(names[:3]) + ('…' if len(names) > 3 else '')
                lines.append(f'• {err}（{len(names)} 人：{preview}）')
            if len(err_groups) > 5:
                lines.append(f'…还有 {len(err_groups) - 5} 类错误，详情见发送记录')
            flash('\n'.join(lines), 'warning' if success > 0 else 'error')
        return redirect(url_for('email.logs'))

    employees = Employee.query.order_by(Employee.employee_id).all()
    tpls = EmailTemplate.query.order_by(EmailTemplate.name).all()
    missing = [e for e in employees if not e.email]
    emp_info = employee_personnel_snapshot(employees)
    return render_template('email_send.html', employees=employees,
                           templates=tpls, missing_email=missing,
                           emp_info=emp_info)


# ── 定时任务 ───────────────────────────────────────────────

def _schedule_from_form(existing=None):
    name = request.form.get('name', '').strip()
    template_id = request.form.get('template_id')
    schedule_type = request.form.get('schedule_type', 'daily')
    run_time = request.form.get('run_time', '09:00').strip() or '09:00'
    day_of_month = request.form.get('day_of_month')
    run_date = request.form.get('run_date')
    recipient_mode = request.form.get('recipient_mode', 'ids')
    enabled = request.form.get('enabled') == '1'
    if not name or not template_id:
        flash('任务名称和邮件模板为必填', 'error')
        return None
    try:
        template_id = int(template_id)
    except ValueError:
        flash('邮件模板无效', 'error')
        return None
    recipient_ids = None
    recipient_filter = None
    if recipient_mode == 'ids':
        recipient_ids = request.form.getlist('recipient_ids')
        if not recipient_ids:
            flash('请至少选择一位收件人', 'error')
            return None
    elif recipient_mode == 'filter':
        recipient_filter = {
            'rank_min': request.form.get('rank_min') or None,
            'rank_max': request.form.get('rank_max') or None,
            'remote': request.form.get('remote_status') or 'any',
            'remote_from': request.form.get('remote_from') or None,
            'remote_to': request.form.get('remote_to') or None,
        }
    day_of_month_v = None
    run_date_v = None
    if schedule_type == 'monthly':
        try:
            day_of_month_v = int(day_of_month)
            if not 1 <= day_of_month_v <= 31:
                raise ValueError
        except (TypeError, ValueError):
            flash('每月日期需为 1–31 的整数', 'error')
            return None
    elif schedule_type == 'once':
        if not run_date:
            flash('单次任务需选择日期', 'error')
            return None
        try:
            run_date_v = datetime.fromisoformat(run_date).date()
        except ValueError:
            flash('执行日期格式无效', 'error')
            return None
    target = existing or EmailSchedule()
    target.name = name
    target.template_id = template_id
    target.schedule_type = schedule_type
    target.run_time = run_time
    target.day_of_month = day_of_month_v
    target.run_date = run_date_v
    target.recipient_mode = recipient_mode
    target.recipient_ids = recipient_ids
    target.recipient_filter = recipient_filter
    target.enabled = enabled
    return target


@email_bp.route('/schedules')
def schedules():
    scheds = EmailSchedule.query.order_by(EmailSchedule.created_at.desc()).all()
    tpl_map = {t.id: t.name for t in EmailTemplate.query.all()}
    return render_template('email_schedules.html', schedules=scheds, templates=tpl_map)


@email_bp.route('/schedules/new', methods=['GET', 'POST'])
def schedule_new():
    if request.method == 'POST':
        sched = _schedule_from_form()
        if sched is None:
            return redirect(url_for('email.schedule_new'))
        db.session.add(sched)
        db.session.commit()
        scheduler_module.add_or_update_job(sched)
        flash('定时任务已创建', 'success')
        return redirect(url_for('email.schedules'))
    return render_template('email_schedule_edit.html', sched=None,
                           templates=EmailTemplate.query.order_by(EmailTemplate.name).all(),
                           employees=Employee.query.order_by(Employee.employee_id).all())


@email_bp.route('/schedules/<int:id>/edit', methods=['GET', 'POST'])
def schedule_edit(id):
    sched = EmailSchedule.query.get_or_404(id)
    if request.method == 'POST':
        if _schedule_from_form(existing=sched) is None:
            return redirect(url_for('email.schedule_edit', id=id))
        db.session.commit()
        scheduler_module.add_or_update_job(sched)
        flash('定时任务已更新', 'success')
        return redirect(url_for('email.schedules'))
    return render_template('email_schedule_edit.html', sched=sched,
                           templates=EmailTemplate.query.order_by(EmailTemplate.name).all(),
                           employees=Employee.query.order_by(Employee.employee_id).all())


@email_bp.route('/schedules/<int:id>/delete', methods=['POST'])
def schedule_delete(id):
    sched = EmailSchedule.query.get_or_404(id)
    scheduler_module.remove_job(sched.id)
    db.session.delete(sched)
    db.session.commit()
    flash('定时任务已删除', 'success')
    return redirect(url_for('email.schedules'))


@email_bp.route('/schedules/<int:id>/toggle', methods=['POST'])
def schedule_toggle(id):
    sched = EmailSchedule.query.get_or_404(id)
    sched.enabled = not sched.enabled
    db.session.commit()
    scheduler_module.add_or_update_job(sched)
    flash(('已启用' if sched.enabled else '已停用') + f'任务「{sched.name}」', 'success')
    return redirect(url_for('email.schedules'))


@email_bp.route('/schedules/<int:id>/run-now', methods=['POST'])
def schedule_run_now(id):
    sched = EmailSchedule.query.get_or_404(id)
    try:
        run_email_schedule(sched)
        sched.last_run_at = beijing_now()
        db.session.commit()
        # 从最新一条 log 看实际发送结果
        last = (EmailLog.query.filter_by(schedule_id=sched.id)
                .order_by(EmailLog.sent_at.desc()).first())
        if last and last.fail_count > 0:
            errs = set()
            for v in (last.details or {}).values():
                if v != 'ok':
                    errs.add(v)
            preview = '；'.join(list(errs)[:3])
            flash(f'已执行：成功 {last.success_count}，失败 {last.fail_count}。错误：{preview}', 'warning')
        else:
            flash('已立即执行一次', 'success')
    except Exception as e:
        flash(f'执行失败：{e}', 'error')
    return redirect(url_for('email.schedules'))


# ── 发送记录 ───────────────────────────────────────────────

@email_bp.route('/logs')
def logs():
    records = EmailLog.query.order_by(EmailLog.sent_at.desc()).all()
    return render_template('email_logs.html', logs=records)


# ── 数据备份 ───────────────────────────────────────────────

@email_bp.route('/backup', methods=['GET', 'POST'])
def backup():
    cfg = BackupConfig.query.first()
    if request.method == 'POST':
        enabled = request.form.get('enabled') == '1'
        schedule_type = request.form.get('schedule_type', 'daily')
        run_time = request.form.get('run_time', '02:00').strip() or '02:00'
        day_of_week = request.form.get('day_of_week')
        day_of_month = request.form.get('day_of_month')
        recipients = request.form.get('recipients', '').strip()
        subject = request.form.get('subject', '').strip() or None

        if enabled and not recipients:
            flash('启用备份前请填写收件人邮箱', 'error')
            return redirect(url_for('email.backup'))

        dow = None
        dom = None
        if schedule_type == 'weekly':
            try:
                dow = int(day_of_week)
                if not 0 <= dow <= 6:
                    raise ValueError
            except (TypeError, ValueError):
                flash('请选择有效的星期（0 周一 ~ 6 周日）', 'error')
                return redirect(url_for('email.backup'))
        elif schedule_type == 'monthly':
            try:
                dom = int(day_of_month)
                if not 1 <= dom <= 31:
                    raise ValueError
            except (TypeError, ValueError):
                flash('每月日期需为 1–31 的整数', 'error')
                return redirect(url_for('email.backup'))

        if cfg is None:
            cfg = BackupConfig()
            db.session.add(cfg)
        cfg.enabled = enabled
        cfg.schedule_type = schedule_type
        cfg.run_time = run_time
        cfg.day_of_week = dow
        cfg.day_of_month = dom
        cfg.recipients = recipients
        cfg.subject = subject
        db.session.commit()
        scheduler_module.refresh_backup_job(cfg)
        flash('备份配置已保存', 'success')
        return redirect(url_for('email.backup'))
    return render_template('email_backup.html', cfg=cfg)


@email_bp.route('/backup/run-now', methods=['POST'])
def backup_run_now():
    cfg = BackupConfig.query.first()
    if not cfg:
        flash('请先保存备份配置', 'warning')
        return redirect(url_for('email.backup'))
    try:
        success, fail, _ = run_backup(cfg)
        if fail == 0:
            flash(f'已成功发送备份邮件到 {success} 个收件人', 'success')
        else:
            flash(f'备份发送完成：成功 {success}，失败 {fail}（详情见发送记录）', 'warning')
    except Exception as e:
        flash(f'备份失败：{e}', 'error')
    return redirect(url_for('email.backup'))
