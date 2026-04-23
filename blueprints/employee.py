#!/usr/bin/env python3
"""员工基本信息表（姓名/工号/邮箱）。"""
import io
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
import pandas as pd
from pypinyin import lazy_pinyin, Style

from models import db, Employee, EmailTemplate


employee_bp = Blueprint('employee', __name__, url_prefix='/employees')

DEFAULT_EMAIL_SUFFIX = '@cmss.chinamobile.com'


def _default_email(name):
    """根据中文姓名生成默认邮箱：拼音(name)@cmss.chinamobile.com。无法转拼音时返回空。"""
    if not name:
        return ''
    py = ''.join(lazy_pinyin(name.strip(), style=Style.NORMAL))
    py = ''.join(c for c in py if c.isalnum()).lower()
    return f'{py}{DEFAULT_EMAIL_SUFFIX}' if py else ''


@employee_bp.route('/')
def list_view():
    employees = Employee.query.order_by(Employee.employee_id).all()
    email_templates_list = EmailTemplate.query.order_by(EmailTemplate.name).all()
    return render_template('employees.html', employees=employees,
                           email_templates_list=email_templates_list)


@employee_bp.route('/add', methods=['POST'])
def add():
    emp_id = request.form.get('employee_id', '').strip()
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    if not emp_id or not name:
        flash('员工编号和姓名必填', 'error')
        return redirect(url_for('employee.list_view'))
    if Employee.query.get(emp_id):
        flash(f'员工编号 {emp_id} 已存在', 'error')
        return redirect(url_for('employee.list_view'))
    db.session.add(Employee(employee_id=emp_id, name=name, email=email or None))
    db.session.commit()
    flash(f'员工 {name} 已添加', 'success')
    return redirect(url_for('employee.list_view'))


@employee_bp.route('/edit/<employee_id>', methods=['POST'])
def edit(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    emp.name = request.form.get('name', emp.name).strip()
    emp.email = request.form.get('email', '').strip() or None
    db.session.commit()
    flash('员工信息已更新', 'success')
    return redirect(url_for('employee.list_view'))


@employee_bp.route('/fill_default_email', methods=['POST'])
def fill_default_email():
    """为邮箱为空的员工批量填充默认邮箱。
      scope=missing  → 所有邮箱为空的员工
      scope=selected → 仅已勾选且邮箱为空的员工
    已有邮箱者保持不变。
    """
    scope = request.form.get('scope', 'missing')
    query = Employee.query.filter(
        db.or_(Employee.email.is_(None), Employee.email == '')
    )
    if scope == 'selected':
        selected_ids = request.form.getlist('employee_ids')
        if not selected_ids:
            flash('请先勾选员工再选择"仅为已选中的员工填充"', 'warning')
            return redirect(url_for('employee.list_view'))
        query = query.filter(Employee.employee_id.in_(selected_ids))

    updated, skipped = 0, 0
    for emp in query.all():
        email = _default_email(emp.name)
        if not email:
            skipped += 1
            continue
        emp.email = email
        updated += 1
    db.session.commit()

    msg = f'已为 {updated} 位员工填充默认邮箱'
    if skipped:
        msg += f'；{skipped} 位姓名无法转换为拼音已跳过'
    flash(msg, 'success' if updated else 'warning')
    return redirect(url_for('employee.list_view'))


@employee_bp.route('/delete/<employee_id>', methods=['POST'])
def delete(employee_id):
    emp = Employee.query.get_or_404(employee_id)
    db.session.delete(emp)
    db.session.commit()
    flash('员工已删除', 'success')
    return redirect(url_for('employee.list_view'))


@employee_bp.route('/bulk_delete', methods=['POST'])
def bulk_delete():
    ids = request.form.getlist('employee_ids')
    if not ids:
        flash('未选择任何员工', 'warning')
        return redirect(url_for('employee.list_view'))
    deleted = Employee.query.filter(Employee.employee_id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    flash(f'已删除 {deleted} 位员工', 'success')
    return redirect(url_for('employee.list_view'))


@employee_bp.route('/export')
def export():
    fmt = request.args.get('format', 'xlsx')
    employees = Employee.query.order_by(Employee.employee_id).all()
    rows = [{'员工编号': e.employee_id, '姓名': e.name, '邮箱': e.email or ''} for e in employees]
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    fname = f'员工列表_{datetime.now().strftime("%Y%m%d")}'
    if fmt == 'csv':
        df.to_csv(buf, index=False, encoding='utf-8-sig')
        buf.seek(0)
        return send_file(buf, mimetype='text/csv', as_attachment=True, download_name=f'{fname}.csv')
    # xlsx：把"员工编号"列显式标为文本格式，避免 Excel 打开后把 "0001234" 之类视为数字
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
        ws = writer.sheets['Sheet1']
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=1):
            for cell in row:
                cell.number_format = '@'
    buf.seek(0)
    return send_file(buf,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=f'{fname}.xlsx')


@employee_bp.route('/import', methods=['POST'])
def import_employees():
    file = request.files.get('file')
    if not file:
        flash('请选择文件', 'error')
        return redirect(url_for('employee.list_view'))
    try:
        # 按字符串读取，避免 pandas 把 "0001234" / 员工编号 之类的值推断为数字丢失前导零或变成 "1234.0"
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file, dtype=str, keep_default_na=False)
        elif file.filename.endswith('.xlsx'):
            df = pd.read_excel(file, dtype=str, keep_default_na=False)
        else:
            flash('只支持CSV和Excel文件', 'error')
            return redirect(url_for('employee.list_view'))

        def _cell_str(v):
            if v is None:
                return ''
            if isinstance(v, float):
                return str(int(v)) if v.is_integer() else str(v)
            s = str(v).strip()
            # pandas 把整数 Excel 单元格读成 float 后 str() 会得到 "1234.0"
            if s.endswith('.0') and s[:-2].lstrip('-').isdigit():
                s = s[:-2]
            return s

        imported, skipped = 0, []
        for _, row in df.iterrows():
            emp_id = _cell_str(row.get('员工编号', ''))
            name = _cell_str(row.get('姓名', ''))
            email = _cell_str(row.get('邮箱', ''))
            if not emp_id or not name:
                continue
            if Employee.query.get(emp_id):
                skipped.append(emp_id)
                continue
            db.session.add(Employee(employee_id=emp_id, name=name, email=email or None))
            imported += 1
        db.session.commit()
        if skipped:
            flash(f'导入完成：新增 {imported} 条，以下编号已存在跳过：{", ".join(skipped)}', 'warning')
        else:
            flash(f'导入成功：新增 {imported} 条员工', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{e}', 'error')
    return redirect(url_for('employee.list_view'))


@employee_bp.route('/api/search')
def search():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    results = Employee.query.filter(
        db.or_(Employee.employee_id.contains(q), Employee.name.contains(q))
    ).limit(20).all()
    return jsonify([{'employee_id': e.employee_id, 'name': e.name, 'email': e.email or ''}
                    for e in results])
