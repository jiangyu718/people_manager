#!/usr/bin/env python3
"""人员记录业务逻辑：保存/快照/问卷差分拆分。"""
from datetime import timedelta
from flask import request

from models import db, Personnel, PersonnelHistory, Attachment, Employee
from services.location import compose_location, is_remote_qualified


def calc_transition_end(property_delivery_date):
    """过渡期截止 = 房产交付日期 + 1 年 - 1 天（闰年安全）。"""
    if not property_delivery_date:
        return None
    d = property_delivery_date
    try:
        next_year = d.replace(year=d.year + 1)
    except ValueError:
        # 2.29 → 次年 2.28
        next_year = d.replace(year=d.year + 1, day=28)
    return next_year - timedelta(days=1)


def personnel_snapshot(p):
    def iso(d):
        return d.isoformat() if d else None
    return {
        'personnel_type': p.personnel_type,
        'employee_id': p.employee_id,
        'name': p.name,
        'rank': p.rank,
        'rank_date': iso(p.rank_date),
        'work_location': p.work_location,
        'household_location': p.household_location,
        'spouse_location': p.spouse_location,
        'children_location': p.children_location,
        'has_property': p.has_property,
        'property_delivery_date': iso(p.property_delivery_date),
        'property_all_sold': p.property_all_sold,
        'transition_end_date': iso(p.transition_end_date),
        'remote_start_date': iso(p.remote_start_date),
        'remote_end_date': iso(p.remote_end_date),
        'work_location_date': iso(p.work_location_date),
        'household_location_date': iso(p.household_location_date),
        'spouse_location_date': iso(p.spouse_location_date),
        'notes': p.notes,
        'is_no_change': p.is_no_change,
    }


def ensure_employee(employee_id, name):
    if not Employee.query.get(employee_id):
        db.session.add(Employee(employee_id=employee_id, name=name))
        db.session.commit()


def build_prefill_for_employee(employee_id):
    """据 employee_id 构造问卷预填字典：优先用最新 approved Personnel，否则用 Employee 基本信息。"""
    from services.location import split_location
    latest = (Personnel.query
              .filter_by(employee_id=employee_id, status='approved')
              .order_by(Personnel.created_at.desc()).first())
    if latest:
        wp, wc = split_location(latest.work_location)
        hp, hc = split_location(latest.household_location)
        sp, sc = split_location(latest.spouse_location)
        cp, cc = split_location(latest.children_location)
        return {
            'personnel_type': latest.personnel_type,
            'employee_id': latest.employee_id,
            'name': latest.name,
            'rank': latest.rank,
            'rank_date': latest.rank_date.isoformat() if latest.rank_date else '',
            'work_province': wp, 'work_city': wc,
            'household_province': hp, 'household_city': hc,
            'spouse_province': sp, 'spouse_city': sc,
            'children_province': cp, 'children_city': cc,
            'has_property': latest.has_property,
            'property_delivery_date': latest.property_delivery_date.isoformat() if latest.property_delivery_date else '',
            'property_all_sold': latest.property_all_sold or '',
            'remote_start_date': latest.remote_start_date.isoformat() if latest.remote_start_date else '',
            'notes': latest.notes or '',
            'source_personnel_id': latest.id,
        }
    emp = Employee.query.get(employee_id)
    if emp:
        return {'employee_id': emp.employee_id, 'name': emp.name}
    return {}


def save_personnel_from_form(form, status='pending'):
    """保存表单 + 附件，并写入一条 insert history。"""
    p = Personnel(
        personnel_type=form.personnel_type.data,
        employee_id=form.employee_id.data,
        name=form.name.data,
        rank=form.rank.data,
        rank_date=form.rank_date.data,
        work_location=compose_location(form.work_province.data, form.work_city.data),
        household_location=compose_location(form.household_province.data, form.household_city.data),
        spouse_location=compose_location(form.spouse_province.data, form.spouse_city.data),
        children_location=compose_location(form.children_province.data, form.children_city.data),
        has_property=form.has_property.data,
        property_delivery_date=form.property_delivery_date.data,
        property_all_sold=form.property_all_sold.data or None,
        transition_end_date=calc_transition_end(form.property_delivery_date.data),
        remote_start_date=form.remote_start_date.data,
        remote_end_date=form.remote_end_date.data,
        work_location_date=form.work_location_date.data,
        household_location_date=form.household_location_date.data,
        spouse_location_date=form.spouse_location_date.data,
        notes=form.notes.data or None,
        status=status,
    )
    db.session.add(p)
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

    snap = personnel_snapshot(p)
    snap['files'] = [
        {'id': a.id, 'filename': a.filename, 'content_type': a.content_type}
        for a in p.attachments
    ]
    db.session.add(PersonnelHistory(
        personnel_id=p.id, history_type='insert', data=snap,
    ))
    db.session.commit()
    return p


def _create_pending(form, *, work_location, household_location, spouse_location,
                    work_date, household_date, spouse_date,
                    remote_start_date, remote_end_date, is_no_change):
    p = Personnel(
        personnel_type=form.personnel_type.data,
        employee_id=form.employee_id.data,
        name=form.name.data,
        rank=form.rank.data,
        rank_date=form.rank_date.data,
        work_location=work_location,
        household_location=household_location,
        spouse_location=spouse_location,
        children_location=compose_location(form.children_province.data, form.children_city.data),
        has_property=form.has_property.data,
        property_delivery_date=form.property_delivery_date.data,
        property_all_sold=form.property_all_sold.data or None,
        transition_end_date=calc_transition_end(form.property_delivery_date.data),
        remote_start_date=remote_start_date,
        remote_end_date=remote_end_date,
        work_location_date=work_date,
        household_location_date=household_date,
        spouse_location_date=spouse_date,
        notes=form.notes.data or None,
        is_no_change=is_no_change,
        status='pending',
    )
    db.session.add(p)
    db.session.commit()
    db.session.add(PersonnelHistory(
        personnel_id=p.id, history_type='insert',
        data=personnel_snapshot(p),
    ))
    db.session.commit()
    return p


def save_prefill_submission(form, prefill, attachments_changed=False):
    """非首次问卷：检测 3 个地点变化，按变更时间拆分为多条待审核记录。"""
    latest = (Personnel.query
              .filter_by(employee_id=prefill.get('employee_id'), status='approved')
              .order_by(Personnel.created_at.desc()).first())

    new_work = compose_location(form.work_province.data, form.work_city.data)
    new_household = compose_location(form.household_province.data, form.household_city.data)
    new_spouse = compose_location(form.spouse_province.data, form.spouse_city.data)

    base_work = latest.work_location if latest else new_work
    base_household = latest.household_location if latest else new_household
    base_spouse = latest.spouse_location if latest else new_spouse

    changes = []
    if new_work != base_work and form.work_location_date.data:
        changes.append(('work', form.work_location_date.data, new_work))
    if new_household != base_household and form.household_location_date.data:
        changes.append(('household', form.household_location_date.data, new_household))
    if new_spouse != base_spouse and form.spouse_location_date.data:
        changes.append(('spouse', form.spouse_location_date.data, new_spouse))

    if not changes:
        # 无地点变更，但子女常住地/职级/房产相关信息发生变化时，也要落一条待审记录。
        new_children = compose_location(form.children_province.data, form.children_city.data)
        base_children = latest.children_location if latest else new_children
        base_rank = latest.rank if latest else None
        base_has_property = latest.has_property if latest else None
        base_all_sold = latest.property_all_sold if latest else None
        base_delivery = latest.property_delivery_date if latest else None
        new_all_sold = form.property_all_sold.data or None
        if (new_children != base_children
                or form.rank.data != base_rank
                or form.has_property.data != base_has_property
                or new_all_sold != base_all_sold
                or form.property_delivery_date.data != base_delivery
                or attachments_changed):
            remote = is_remote_qualified(base_work, base_household, base_spouse)
            p = _create_pending(
                form,
                work_location=base_work, household_location=base_household,
                spouse_location=base_spouse,
                work_date=latest.work_location_date if latest else None,
                household_date=latest.household_location_date if latest else None,
                spouse_date=latest.spouse_location_date if latest else None,
                remote_start_date=latest.remote_start_date if latest else None,
                remote_end_date=latest.remote_end_date if latest else None,
                is_no_change=not remote,
            )
            return [p]
        return []

    changes.sort(key=lambda c: c[1])
    cur_work, cur_household, cur_spouse = base_work, base_household, base_spouse
    cur_work_date = latest.work_location_date if latest else None
    cur_household_date = latest.household_location_date if latest else None
    cur_spouse_date = latest.spouse_location_date if latest else None
    created = []
    for field, adj_date, new_val in changes:
        if field == 'work':
            cur_work, cur_work_date = new_val, adj_date
        elif field == 'household':
            cur_household, cur_household_date = new_val, adj_date
        elif field == 'spouse':
            cur_spouse, cur_spouse_date = new_val, adj_date
        remote = is_remote_qualified(cur_work, cur_household, cur_spouse)
        p = _create_pending(
            form,
            work_location=cur_work, household_location=cur_household, spouse_location=cur_spouse,
            work_date=cur_work_date, household_date=cur_household_date, spouse_date=cur_spouse_date,
            remote_start_date=adj_date if remote else None,
            remote_end_date=adj_date if not remote else None,
            is_no_change=False,
        )
        created.append(p)
    return created
