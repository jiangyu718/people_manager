#!/usr/bin/env python3
"""APScheduler 集成：定时发送邮件任务。"""
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger


_scheduler = None
_app = None


def init_scheduler(app):
    global _scheduler, _app
    if _scheduler is not None:
        return
    _app = app
    _scheduler = BackgroundScheduler(timezone=app.config.get('SCHEDULER_TIMEZONE', 'Asia/Shanghai'))
    _scheduler.start()
    with app.app_context():
        reload_all_jobs()


def get_scheduler():
    return _scheduler


def _parse_time(run_time):
    try:
        h, m = run_time.split(':')
        return int(h), int(m)
    except Exception:
        return 9, 0


def _job_id(sched_id):
    return f'email_schedule_{sched_id}'


def reload_all_jobs():
    from models import EmailSchedule, BackupConfig
    if not _scheduler:
        return
    for job in list(_scheduler.get_jobs()):
        if job.id.startswith('email_schedule_') or job.id == _BACKUP_JOB_ID:
            _scheduler.remove_job(job.id)
    for sched in EmailSchedule.query.filter_by(enabled=True).all():
        _add_job(sched)
    cfg = BackupConfig.query.first()
    if cfg and cfg.enabled:
        _add_backup_job(cfg)


def _add_job(sched):
    from models import beijing_now
    tz = _scheduler.timezone
    h, m = _parse_time(sched.run_time)
    trigger = None
    if sched.schedule_type == 'daily':
        trigger = CronTrigger(hour=h, minute=m, timezone=tz)
    elif sched.schedule_type == 'monthly' and sched.day_of_month:
        trigger = CronTrigger(day=sched.day_of_month, hour=h, minute=m, timezone=tz)
    elif sched.schedule_type == 'once' and sched.run_date:
        run_at = datetime.combine(sched.run_date, datetime.min.time()).replace(hour=h, minute=m)
        if run_at <= beijing_now():
            return
        trigger = DateTrigger(run_date=run_at, timezone=tz)
    if trigger is None:
        return
    _scheduler.add_job(_run_schedule, trigger=trigger, id=_job_id(sched.id),
                       args=[sched.id], replace_existing=True, misfire_grace_time=3600)


def add_or_update_job(sched):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(_job_id(sched.id))
    except Exception:
        pass
    if sched.enabled:
        _add_job(sched)


def remove_job(sched_id):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(_job_id(sched_id))
    except Exception:
        pass


def _run_schedule(sched_id):
    from models import db, EmailSchedule, beijing_now
    from services.email_service import run_email_schedule
    with _app.app_context():
        sched = EmailSchedule.query.get(sched_id)
        if not sched or not sched.enabled:
            return
        try:
            run_email_schedule(sched)
            sched.last_run_at = beijing_now()
            if sched.schedule_type == 'once':
                sched.enabled = False
            db.session.commit()
        except Exception as e:
            _app.logger.error(f'[email_schedule {sched_id}] run failed: {e}')


# ── 数据备份定时任务 ───────────────────────────────────────
_BACKUP_JOB_ID = 'backup_schedule'


def _add_backup_job(cfg):
    tz = _scheduler.timezone
    h, m = _parse_time(cfg.run_time)
    trigger = None
    if cfg.schedule_type == 'daily':
        trigger = CronTrigger(hour=h, minute=m, timezone=tz)
    elif cfg.schedule_type == 'weekly' and cfg.day_of_week is not None:
        trigger = CronTrigger(day_of_week=int(cfg.day_of_week), hour=h, minute=m, timezone=tz)
    elif cfg.schedule_type == 'monthly' and cfg.day_of_month:
        trigger = CronTrigger(day=cfg.day_of_month, hour=h, minute=m, timezone=tz)
    if trigger is None:
        return
    _scheduler.add_job(_run_backup, trigger=trigger, id=_BACKUP_JOB_ID,
                       replace_existing=True, misfire_grace_time=3600)


def refresh_backup_job(cfg):
    """BackupConfig 变更后调用。"""
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(_BACKUP_JOB_ID)
    except Exception:
        pass
    if cfg and cfg.enabled:
        _add_backup_job(cfg)


def _run_backup():
    from models import BackupConfig
    from services.backup_service import run_backup
    with _app.app_context():
        cfg = BackupConfig.query.first()
        if not cfg or not cfg.enabled:
            return
        try:
            run_backup(cfg)
        except Exception as e:
            _app.logger.error(f'[backup] run failed: {e}')
