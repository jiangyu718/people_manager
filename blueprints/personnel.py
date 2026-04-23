#!/usr/bin/env python3
"""人员/异地记录：列表、增/改/删、审核、历史、垃圾桶、导入、导出。"""
from datetime import datetime
import io
import re
import zipfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
import pandas as pd

from models import db, Personnel, PersonnelHistory, Trash, Attachment, beijing_now
from forms import PersonnelForm
from utils import CHINA_CITIES
from services.location import compose_location, split_location
from services.form_helpers import inject_city_choices, flash_form_errors, validate_personnel_inputs
from services.personnel_service import (
    save_personnel_from_form, ensure_employee, personnel_snapshot,
    calc_transition_end,
)


personnel_bp = Blueprint('personnel', __name__)


_DATE_FORMATS = (
    '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d',
    '%Y年%m月%d日', '%Y年%m月%d',
    '%d-%m-%Y', '%d/%m/%Y',
    '%m/%d/%Y', '%m-%d-%Y',
)


def _parse_date(s):
    """尽量宽容的日期解析：支持 YYYY-MM-DD / YYYY/MM/DD / YYYY年M月D日 等。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    s2 = (s.replace('／', '/').replace('－', '-').replace('．', '.')
           .replace(' ', ''))
    # Excel 读取后可能为 "2024-05-15 00:00:00"，只保留日期部分
    s2 = s2.split('T')[0].split(' ')[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s2, fmt).date()
        except ValueError:
            continue
    # 宽松兜底：抽出 年/月/日 三段数字
    m = re.match(r'^(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*$', s2)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def _preserve_form_dates():
    """POST 校验失败后重新渲染表单时保留用户已填写的日期值。"""
    return {
        'property': request.form.get('property_delivery_date', ''),
        'remote_start': request.form.get('remote_start_date', ''),
        'remote_end': request.form.get('remote_end_date', ''),
        'work_loc': request.form.get('work_location_date', ''),
        'household_loc': request.form.get('household_location_date', ''),
        'spouse_loc': request.form.get('spouse_location_date', ''),
        'created_at': request.form.get('created_at_edit', ''),
    }


# ── 附件删除（通用，供人员 / 邮件模板表单内使用，避免嵌套 form）──

@personnel_bp.route('/attachment/<int:att_id>/delete', methods=['POST'])
def attachment_delete(att_id):
    att = Attachment.query.get_or_404(att_id)
    db.session.delete(att)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'fetch' \
            or 'application/json' in request.headers.get('Accept', ''):
        return jsonify({'ok': True, 'id': att_id})
    flash('附件已删除', 'success')
    if att.email_template_id:
        return redirect(url_for('email.template_edit', id=att.email_template_id))
    if att.personnel_id:
        return redirect(url_for('personnel.edit', id=att.personnel_id))
    return redirect(url_for('personnel.list_view'))


# ── 列表 / 导出 ─────────────────────────────────────────────

@personnel_bp.route('/list')
def list_view():
    # 排序：先按员工编号升序，再按异地开始时间倒序（近 → 远，空值排最后）
    personnels = (Personnel.query.filter_by(status='approved')
                  .order_by(Personnel.employee_id.asc(),
                            Personnel.remote_start_date.is_(None),
                            Personnel.remote_start_date.desc(),
                            Personnel.created_at.desc())
                  .all())
    return render_template('list.html', personnels=personnels)


@personnel_bp.route('/list/export')
def export():
    fmt = request.args.get('format', 'csv')
    personnels = (Personnel.query.filter_by(status='approved')
                  .order_by(Personnel.employee_id.asc(),
                            Personnel.remote_start_date.is_(None),
                            Personnel.remote_start_date.desc(),
                            Personnel.created_at.desc())
                  .all())
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
    fname = f'异地情况记录_{datetime.now().strftime("%Y%m%d")}'
    buf = io.BytesIO()
    if fmt == 'xlsx':
        df.to_excel(buf, index=False)
        buf.seek(0)
        return send_file(buf,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=f'{fname}.xlsx')
    df.to_csv(buf, index=False, encoding='utf-8-sig')
    buf.seek(0)
    return send_file(buf, mimetype='text/csv', as_attachment=True,
                     download_name=f'{fname}.csv')


# ── 增 / 改 ─────────────────────────────────────────────────

@personnel_bp.route('/add', methods=['GET', 'POST'])
def add():
    form = PersonnelForm()
    clone_from_id = ''
    clone_src = None
    if request.method == 'POST':
        inject_city_choices(form)
        clone_from_id = request.form.get('clone_from_id', '').strip()
        clone_src = (Personnel.query.get(int(clone_from_id))
                     if clone_from_id.isdigit() else None)
    if form.validate_on_submit():
        extra_errors = validate_personnel_inputs(form, personnel=None, clone_src=clone_src)
        if extra_errors:
            for msg in extra_errors:
                flash(msg, 'error')
            return render_template('add.html', form=form,
                                   prefill_dates=_preserve_form_dates(),
                                   prefill_has_property=request.form.get('has_property', ''),
                                   clone_from_id=clone_from_id,
                                   clone_src=clone_src)
        p = save_personnel_from_form(form, status='approved')
        if clone_src:
            exclude_ids = {
                int(x) for x in request.form.getlist('clone_exclude_ids') if x.isdigit()
            }
            for att in clone_src.attachments:
                if att.id not in exclude_ids:
                    db.session.add(Attachment(
                        filename=att.filename, content_type=att.content_type,
                        data=att.data, personnel_id=p.id, category=att.category,
                    ))
            db.session.commit()
        ensure_employee(p.employee_id, p.name)
        flash('异地情况记录添加成功', 'success')
        return redirect(url_for('personnel.list_view'))
    if request.method == 'POST':
        flash_form_errors(form)
        return render_template('add.html', form=form,
                               prefill_dates=_preserve_form_dates(),
                               prefill_has_property=request.form.get('has_property', ''),
                               clone_from_id=clone_from_id,
                               clone_src=clone_src)
    return render_template('add.html', form=form)


@personnel_bp.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    p = Personnel.query.get_or_404(id)
    form = PersonnelForm()
    if request.method == 'POST':
        inject_city_choices(form)
        if form.validate_on_submit():
            extra_errors = validate_personnel_inputs(form, personnel=p)
            if extra_errors:
                for msg in extra_errors:
                    flash(msg, 'error')
                return render_template('edit.html', form=form, personnel=p,
                                       property_date=request.form.get('property_delivery_date', ''),
                                       remote_date=request.form.get('remote_start_date', ''),
                                       remote_end_date=request.form.get('remote_end_date', ''),
                                       work_location_date=request.form.get('work_location_date', ''),
                                       household_location_date=request.form.get('household_location_date', ''),
                                       spouse_location_date=request.form.get('spouse_location_date', ''),
                                       rank_date=request.form.get('rank_date', ''))
            p.personnel_type = form.personnel_type.data
            p.employee_id = form.employee_id.data
            p.name = form.name.data
            p.rank = form.rank.data
            p.rank_date = form.rank_date.data
            p.work_location = compose_location(form.work_province.data, form.work_city.data)
            p.household_location = compose_location(form.household_province.data, form.household_city.data)
            p.spouse_location = compose_location(form.spouse_province.data, form.spouse_city.data)
            p.children_location = compose_location(form.children_province.data, form.children_city.data)
            p.has_property = form.has_property.data
            p.property_delivery_date = form.property_delivery_date.data
            p.property_all_sold = form.property_all_sold.data or None
            p.transition_end_date = calc_transition_end(form.property_delivery_date.data)
            p.remote_start_date = form.remote_start_date.data
            p.remote_end_date = form.remote_end_date.data
            p.work_location_date = form.work_location_date.data
            p.household_location_date = form.household_location_date.data
            p.spouse_location_date = form.spouse_location_date.data
            p.notes = form.notes.data or None
            if form.created_at_edit.data:
                from datetime import datetime as dt
                p.created_at = dt.combine(form.created_at_edit.data, dt.min.time())
            db.session.flush()
            for field_name, cat in (('property_contract', 'property'),
                                    ('household_file', 'household')):
                for f in request.files.getlist(field_name):
                    if f and f.filename:
                        db.session.add(Attachment(
                            filename=f.filename, content_type=f.content_type,
                            data=f.read(), personnel_id=p.id, category=cat,
                        ))
            db.session.commit()
            flash('记录已更新', 'success')
            if p.status == 'pending':
                return redirect(url_for('personnel.review'))
            return redirect(url_for('personnel.list_view'))
        flash_form_errors(form)
        return render_template('edit.html', form=form, personnel=p,
                               property_date=request.form.get('property_delivery_date', ''),
                               remote_date=request.form.get('remote_start_date', ''),
                               remote_end_date=request.form.get('remote_end_date', ''),
                               work_location_date=request.form.get('work_location_date', ''),
                               household_location_date=request.form.get('household_location_date', ''),
                               spouse_location_date=request.form.get('spouse_location_date', ''),
                               rank_date=request.form.get('rank_date', ''))

    wp, wc = split_location(p.work_location)
    hp, hc = split_location(p.household_location)
    sp, sc = split_location(p.spouse_location)
    cp, cc = split_location(p.children_location)
    form.personnel_type.data = p.personnel_type
    form.employee_id.data = p.employee_id
    form.name.data = p.name
    form.rank.data = p.rank
    form.work_province.data = wp
    form.work_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(wp, [])]
    form.work_city.data = wc
    form.household_province.data = hp
    form.household_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(hp, [])]
    form.household_city.data = hc
    form.spouse_province.data = sp
    form.spouse_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(sp, [])]
    form.spouse_city.data = sc
    form.children_province.data = cp
    form.children_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(cp, [])]
    form.children_city.data = cc
    form.has_property.data = p.has_property
    form.property_all_sold.data = p.property_all_sold or ''
    form.notes.data = p.notes or ''
    form.created_at_edit.data = p.created_at.date() if p.created_at else None
    return render_template('edit.html', form=form, personnel=p,
                           property_date=str(p.property_delivery_date) if p.property_delivery_date else '',
                           remote_date=str(p.remote_start_date) if p.remote_start_date else '',
                           remote_end_date=str(p.remote_end_date) if p.remote_end_date else '',
                           work_location_date=str(p.work_location_date) if p.work_location_date else '',
                           household_location_date=str(p.household_location_date) if p.household_location_date else '',
                           spouse_location_date=str(p.spouse_location_date) if p.spouse_location_date else '',
                           rank_date=str(p.rank_date) if p.rank_date else '')


@personnel_bp.route('/clone/<int:id>', methods=['GET'])
def clone(id):
    """基于现有记录预填添加表单。"""
    src = Personnel.query.get_or_404(id)
    form = PersonnelForm()
    inject_city_choices(form)
    wp, wc = split_location(src.work_location)
    hp, hc = split_location(src.household_location)
    sp, sc = split_location(src.spouse_location)
    cp, cc = split_location(src.children_location)
    form.personnel_type.data = src.personnel_type
    form.employee_id.data = src.employee_id
    form.name.data = src.name
    form.rank.data = src.rank
    form.work_province.data = wp
    form.work_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(wp, [])]
    form.work_city.data = wc
    form.household_province.data = hp
    form.household_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(hp, [])]
    form.household_city.data = hc
    form.spouse_province.data = sp
    form.spouse_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(sp, [])]
    form.spouse_city.data = sc
    form.children_province.data = cp
    form.children_city.choices = [('', '请选择市')] + [(c, c) for c in CHINA_CITIES.get(cp, [])]
    form.children_city.data = cc
    form.has_property.data = src.has_property
    form.property_all_sold.data = src.property_all_sold or ''
    form.notes.data = src.notes or ''
    prefill_dates = {
        'property': str(src.property_delivery_date) if src.property_delivery_date else '',
        'remote_start': str(src.remote_start_date) if src.remote_start_date else '',
        'remote_end': str(src.remote_end_date) if src.remote_end_date else '',
        'work_loc': str(src.work_location_date) if src.work_location_date else '',
        'household_loc': str(src.household_location_date) if src.household_location_date else '',
        'spouse_loc': str(src.spouse_location_date) if src.spouse_location_date else '',
    }
    return render_template('add.html', form=form,
                           prefill_dates=prefill_dates,
                           prefill_has_property=src.has_property or '',
                           clone_from_id=src.id,
                           clone_src=src)


@personnel_bp.route('/delete/<int:id>', methods=['POST'])
def delete(id):
    p = Personnel.query.get_or_404(id)
    p.status = 'deleted'
    db.session.commit()
    flash('记录已删除', 'success')
    return redirect(url_for('personnel.list_view'))


@personnel_bp.route('/list/bulk_delete', methods=['POST'])
def bulk_delete():
    ids_raw = request.form.get('ids', '')
    ids = [i.strip() for i in ids_raw.split(',') if i.strip().isdigit()]
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('personnel.list_view'))
    count = 0
    for id_str in ids:
        p = Personnel.query.get(int(id_str))
        if p and p.status == 'approved':
            p.status = 'deleted'
            count += 1
    db.session.commit()
    flash(f'已删除 {count} 条记录', 'success')
    return redirect(url_for('personnel.list_view'))


@personnel_bp.route('/list/bulk_download', methods=['POST'])
def bulk_download():
    ids_raw = request.form.get('ids', '')
    ids = [i.strip() for i in ids_raw.split(',') if i.strip().isdigit()]
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('personnel.list_view'))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for id_str in ids:
            p = Personnel.query.get(int(id_str))
            if not p or p.status != 'approved':
                continue
            for att in p.attachments:
                folder = f'{p.employee_id}_{p.name}'
                zf.writestr(f'{folder}/{att.filename}', att.data)
    buf.seek(0)
    fname = f'附件下载_{datetime.now().strftime("%Y%m%d%H%M%S")}.zip'
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=fname)


# ── 审核 ───────────────────────────────────────────────────

_DIFF_FIELDS = [
    ('personnel_type',        '人员类型'),
    ('rank',                  '职级'),
    ('work_location',         '工作所在地'),
    ('household_location',    '户口所在地'),
    ('spouse_location',       '配偶常住地'),
    ('children_location',     '子女常住地'),
    ('has_property',          '是否购房'),
    ('property_delivery_date','房产交付日期'),
    ('property_all_sold',     '房产是否全部售出'),
    ('notes',                 '备注'),
]


def _compute_diff(pending, approved):
    """返回 {attr: label} 字典，只含相对于 approved 发生变化的字段。
    若 approved 为 None（新员工），返回 None 表示全量新增。"""
    if approved is None:
        return None
    changed = {}
    for attr, label in _DIFF_FIELDS:
        if getattr(pending, attr) != getattr(approved, attr):
            changed[attr] = label
    return changed


@personnel_bp.route('/review')
def review():
    personnels = Personnel.query.filter_by(status='pending').order_by(Personnel.created_at.desc()).all()
    diffs = {}
    for p in personnels:
        approved = (Personnel.query
                    .filter_by(employee_id=p.employee_id, status='approved')
                    .order_by(Personnel.created_at.desc()).first())
        diffs[p.id] = _compute_diff(p, approved)
    return render_template('review.html', personnels=personnels, diffs=diffs)


@personnel_bp.route('/approve/<int:id>', methods=['POST'])
def approve(id):
    p = Personnel.query.get_or_404(id)
    p.status = 'approved'
    db.session.commit()
    ensure_employee(p.employee_id, p.name)
    flash('审核通过', 'success')
    return redirect(url_for('personnel.review'))


@personnel_bp.route('/reject/<int:id>', methods=['POST'])
def reject(id):
    p = Personnel.query.get_or_404(id)
    p.status = 'rejected'
    db.session.commit()
    flash('已拒绝，记录已移至历史记录', 'success')
    return redirect(url_for('personnel.review'))


@personnel_bp.route('/review/bulk', methods=['POST'])
def bulk_action():
    action = request.form.get('action')
    ids = request.form.getlist('ids')
    if not ids:
        flash('请勾选要操作的记录', 'warning')
        return redirect(url_for('personnel.review'))
    count = 0
    for id_str in ids:
        try:
            p = Personnel.query.get(int(id_str))
        except (ValueError, TypeError):
            continue
        if p and p.status == 'pending':
            if action == 'approve':
                p.status = 'approved'
                ensure_employee(p.employee_id, p.name)
            elif action == 'reject':
                p.status = 'rejected'
            count += 1
    db.session.commit()
    label = '通过' if action == 'approve' else '拒绝'
    flash(f'已{label} {count} 条记录', 'success')
    return redirect(url_for('personnel.review'))


@personnel_bp.route('/review/approve-all', methods=['POST'])
def approve_all():
    pending = Personnel.query.filter_by(status='pending').all()
    for p in pending:
        p.status = 'approved'
        ensure_employee(p.employee_id, p.name)
    db.session.commit()
    flash(f'已通过全部 {len(pending)} 条待审核记录', 'success')
    return redirect(url_for('personnel.review'))


# ── 历史 ───────────────────────────────────────────────────

@personnel_bp.route('/history')
def history():
    personnels = Personnel.query.order_by(Personnel.created_at.desc()).all()
    return render_template('history.html', personnels=personnels)


@personnel_bp.route('/history/restore/<int:id>', methods=['POST'])
def history_restore(id):
    p = Personnel.query.get_or_404(id)
    if p.status not in ('rejected', 'deleted'):
        flash('只能恢复审核未通过或已删除的记录', 'warning')
        return redirect(url_for('personnel.history'))
    p.status = 'approved'
    db.session.commit()
    ensure_employee(p.employee_id, p.name)
    flash('记录已恢复到异地情况记录', 'success')
    return redirect(url_for('personnel.history'))


@personnel_bp.route('/history/delete/<int:id>', methods=['POST'])
def history_delete(id):
    p = Personnel.query.get_or_404(id)
    if p.status not in ('rejected', 'deleted'):
        flash('只能删除审核未通过或已删除的记录', 'warning')
        return redirect(url_for('personnel.history'))
    db.session.add(Trash(personnel_id=p.id, data=personnel_snapshot(p)))
    db.session.delete(p)
    db.session.commit()
    flash('记录已移至垃圾桶', 'success')
    return redirect(url_for('personnel.history'))


# ── 垃圾桶 ─────────────────────────────────────────────────

@personnel_bp.route('/trash')
def trash():
    trashes = Trash.query.order_by(Trash.deleted_at.desc()).all()
    return render_template('trash.html', trashes=trashes)


@personnel_bp.route('/trash/restore/<int:id>', methods=['POST'])
def trash_restore(id):
    t = Trash.query.get_or_404(id)
    d = t.data
    pd_date = datetime.fromisoformat(d['property_delivery_date']).date() if d.get('property_delivery_date') else None

    def _iso(k):
        return datetime.fromisoformat(d[k]).date() if d.get(k) else None

    p = Personnel(
        personnel_type=d['personnel_type'],
        employee_id=d['employee_id'],
        name=d['name'],
        rank=int(d['rank']) if d.get('rank') not in (None, '') else None,
        work_location=d['work_location'],
        household_location=d['household_location'],
        spouse_location=d.get('spouse_location'),
        children_location=d.get('children_location'),
        has_property=d['has_property'],
        property_delivery_date=pd_date,
        transition_end_date=calc_transition_end(pd_date),
        remote_start_date=_iso('remote_start_date'),
        remote_end_date=_iso('remote_end_date'),
        work_location_date=_iso('work_location_date'),
        household_location_date=_iso('household_location_date'),
        spouse_location_date=_iso('spouse_location_date'),
        status='approved',
    )
    db.session.add(p)
    db.session.delete(t)
    db.session.commit()
    flash('记录已恢复到异地情况记录', 'success')
    return redirect(url_for('personnel.trash'))


@personnel_bp.route('/trash/delete/<int:id>', methods=['POST'])
def trash_delete(id):
    t = Trash.query.get_or_404(id)
    db.session.delete(t)
    db.session.commit()
    flash('记录已永久删除', 'success')
    return redirect(url_for('personnel.trash'))


# ── 导入 ───────────────────────────────────────────────────

@personnel_bp.route('/import', methods=['GET', 'POST'])
def import_upload():
    if request.method == 'POST':
        file = request.files.get('file')
        # 导入模式：append_pending(默认) / append_approved / overwrite
        mode = request.form.get('mode', 'append_pending')
        if mode not in ('append_pending', 'append_approved', 'overwrite'):
            mode = 'append_pending'
        if not file:
            flash('请选择文件', 'error')
            return redirect(url_for('personnel.import_upload'))
        try:
            # 全部按字符串读取：避免 pandas 把 "0001234" 推断成数字丢前导零、把空值读成 NaN
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file, dtype=str, keep_default_na=False)
            elif file.filename.endswith('.xlsx'):
                df = pd.read_excel(file, dtype=str, keep_default_na=False)
            else:
                flash('只支持CSV和Excel文件', 'error')
                return redirect(url_for('personnel.import_upload'))

            status = 'pending' if mode == 'append_pending' else 'approved'
            cleared = 0

            # 全量覆盖：导入前清空所有 Personnel 及其附件
            if mode == 'overwrite':
                Attachment.query.filter(Attachment.personnel_id.isnot(None)).delete(synchronize_session=False)
                cleared = Personnel.query.delete(synchronize_session=False)
                db.session.flush()

            def _str(r, col):
                v = r.get(col)
                if v is None:
                    return None
                s = str(v).strip()
                if not s or s.lower() == 'nan':
                    return None
                # dtype=str 后仍可能出现 "1234.0"（非 Excel 场景），清掉小数尾
                if s.endswith('.0') and s[:-2].lstrip('-').isdigit():
                    s = s[:-2]
                return s

            def _d(r, col):
                s = _str(r, col)
                if not s:
                    return None
                return _parse_date(s)

            def _rank(r, col='职级'):
                s = _str(r, col)
                if not s:
                    return None
                try:
                    return int(float(s))
                except (ValueError, TypeError):
                    return None

            imported = 0
            for _, row in df.iterrows():
                pd_date = _d(row, '房产交付日期')
                p = Personnel(
                    personnel_type=_str(row, '人员类型'),
                    employee_id=_str(row, '员工编号') or '',
                    name=_str(row, '姓名'),
                    rank=_rank(row, '职级'),
                    work_location=_str(row, '工作所在地'),
                    household_location=_str(row, '户口所在地'),
                    spouse_location=_str(row, '配偶常住地'),
                    children_location=_str(row, '子女常住地'),
                    has_property=_str(row, '是否在工作地购置房产'),
                    property_delivery_date=pd_date,
                    property_all_sold=_str(row, '在工作地购置房产是否全部售出'),
                    transition_end_date=calc_transition_end(pd_date),
                    remote_start_date=_d(row, '异地开始时间'),
                    remote_end_date=_d(row, '异地结束时间'),
                    work_location_date=_d(row, '工作所在地时间'),
                    household_location_date=_d(row, '户口所在地时间'),
                    spouse_location_date=_d(row, '配偶常住地时间'),
                    notes=_str(row, '备注'),
                    status=status,
                )
                db.session.add(p)
                db.session.commit()
                db.session.add(PersonnelHistory(
                    personnel_id=p.id, history_type='insert',
                    data=personnel_snapshot(p),
                ))
                db.session.commit()
                imported += 1

            if mode == 'overwrite':
                flash(f'全量覆盖完成：清空 {cleared} 条，新导入 {imported} 条（已直接通过）', 'success')
                return redirect(url_for('personnel.list_view'))
            if mode == 'append_approved':
                flash(f'导入成功：新增 {imported} 条（已直接通过）', 'success')
                return redirect(url_for('personnel.list_view'))
            flash(f'导入成功：新增 {imported} 条（待审核）', 'success')
            return redirect(url_for('personnel.review'))
        except Exception as e:
            db.session.rollback()
            flash(f'导入失败：{str(e)}', 'error')
        return redirect(url_for('personnel.import_upload'))
    return render_template('import.html')
