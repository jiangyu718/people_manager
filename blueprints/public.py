#!/usr/bin/env python3
"""首页 / 问卷链接生成与外部作答 / 附件下载。"""
import io
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, send_file
from flask import flash

from models import db, Personnel, Trash, FormToken, Attachment
from forms import PersonnelForm
from services.form_helpers import inject_city_choices, flash_form_errors
from services.location import compose_location
from services.personnel_service import (
    save_personnel_from_form, save_prefill_submission, build_prefill_for_employee,
)


def _external_form_extra_errors(form, prefill, is_prefill_mode, prefill_src=None):
    """外部问卷提交的补充校验：户口材料 / 房产材料要求。"""
    errors = []
    exclude_ids = set(int(i) for i in request.form.getlist('clone_exclude_ids') if i.isdigit())

    def retained_count(category):
        if not prefill_src:
            return 0
        return sum(1 for a in prefill_src.attachments
                   if a.id not in exclude_ids and a.category == category)

    new_household_files = [f for f in request.files.getlist('household_file') if f and f.filename]
    has_household = bool(new_household_files) or retained_count('household') > 0

    if is_prefill_mode:
        new_household = compose_location(form.household_province.data,
                                         form.household_city.data)
        prefill_household = compose_location(prefill.get('household_province'),
                                             prefill.get('household_city'))
        if new_household != prefill_household and not has_household:
            errors.append('调整户口所在地后，需重新上传户口本材料（户口本首页、个人页）')
    else:
        if not has_household:
            errors.append('填写户口所在地后，需上传户口本材料（户口本首页、个人页）')

    if form.has_property.data == '是':
        if not form.property_delivery_date.data:
            errors.append('是否在工作地购置房产为"是"时，房产交付日期必填')
        all_sold = (form.property_all_sold.data or '').strip()
        if not all_sold:
            errors.append('是否在工作地购置房产为"是"时，在工作地购置房产是否全部售出必填')
        new_property_files = [f for f in request.files.getlist('property_contract') if f and f.filename]
        if not new_property_files and retained_count('property') == 0:
            errors.append('是否在工作地购置房产为"是"时，需上传房产材料（房产证或购房合同）')

    return errors


def _save_form_attachments(records, prefill_src, exclude_ids, has_property):
    """Copy retained source attachments + new uploaded files to all records."""
    if not records:
        return
    if prefill_src:
        for att in prefill_src.attachments:
            if att.id in exclude_ids:
                continue
            if att.category == 'property' and has_property != '是':
                continue
            for p in records:
                db.session.add(Attachment(
                    filename=att.filename, content_type=att.content_type,
                    data=att.data, personnel_id=p.id, category=att.category,
                ))
    for field_name, cat in (('property_contract', 'property'), ('household_file', 'household')):
        if cat == 'property' and has_property != '是':
            continue
        files_data = [(f.filename, f.content_type, f.read())
                      for f in request.files.getlist(field_name) if f and f.filename]
        for fname, ftype, fdata in files_data:
            for p in records:
                db.session.add(Attachment(
                    filename=fname, content_type=ftype,
                    data=fdata, personnel_id=p.id, category=cat,
                ))
    db.session.commit()


public_bp = Blueprint('public', __name__)


@public_bp.route('/')
def index():
    total = Personnel.query.filter_by(status='approved').count()
    pending = Personnel.query.filter_by(status='pending').count()
    history_count = Personnel.query.filter(
        Personnel.status.in_(['rejected', 'deleted'])
    ).count()
    trash_count = Trash.query.count()
    return render_template('index.html', total=total, pending=pending,
                           history_count=history_count, trash_count=trash_count)


@public_bp.route('/file/<int:file_id>')
def get_file(file_id):
    rec = Attachment.query.get_or_404(file_id)
    return send_file(
        io.BytesIO(rec.data),
        mimetype=rec.content_type,
        as_attachment=False,
        download_name=rec.filename,
    )


@public_bp.route('/generate-form', methods=['GET', 'POST'])
def generate_form():
    if request.method == 'POST':
        employee_id = request.form.get('employee_id', '').strip()
        prefill_data = build_prefill_for_employee(employee_id) if employee_id else {}

        token = str(uuid.uuid4())
        db.session.add(FormToken(token=token, employee_id=employee_id, prefill_data=prefill_data))
        db.session.commit()
        form_url = url_for('public.external_form', token=token, _external=True)
        return render_template('generate_form.html', form_url=form_url, generated=True)

    return render_template('generate_form.html')


@public_bp.route('/form/<token>', methods=['GET', 'POST'])
def external_form(token):
    form_token = FormToken.query.filter_by(token=token).first()
    if not form_token:
        return render_template('form_submitted.html', message='链接无效或已过期')
    if form_token.is_used:
        return render_template('form_submitted.html', message='问卷已经提交！')

    prefill = form_token.prefill_data or {}
    is_prefill_mode = bool(prefill.get('personnel_type'))
    src_id = prefill.get('source_personnel_id')
    prefill_src = Personnel.query.get(src_id) if src_id else None

    form = PersonnelForm()
    if request.method == 'POST':
        inject_city_choices(form)
    if form.validate_on_submit():
        extra_errors = _external_form_extra_errors(form, prefill, is_prefill_mode, prefill_src)
        if extra_errors:
            for msg in extra_errors:
                flash(msg, 'error')
            return render_template('external_form.html', form=form, prefill=prefill,
                                   prefill_src=prefill_src)
        try:
            form_token.is_used = True
            exclude_ids = set(int(i) for i in request.form.getlist('clone_exclude_ids') if i.isdigit())
            if is_prefill_mode:
                new_files = [f for f in request.files.getlist('household_file')
                             + request.files.getlist('property_contract') if f and f.filename]
                attachments_changed = bool(new_files or exclude_ids)
                created = save_prefill_submission(form, prefill, attachments_changed=attachments_changed) or []
                _save_form_attachments(created, prefill_src, exclude_ids, form.has_property.data)
            else:
                save_personnel_from_form(form, status='pending')
        except Exception:
            db.session.rollback()
            return render_template('form_submitted.html',
                                   message='提交失败，请联系管理员')
        return render_template('form_submitted.html',
                               message='提交成功！您的信息已提交，感谢您的配合。')
    if request.method == 'POST':
        flash_form_errors(form)
    return render_template('external_form.html', form=form, prefill=prefill,
                           prefill_src=prefill_src)
