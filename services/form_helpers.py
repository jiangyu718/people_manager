#!/usr/bin/env python3
"""表单校验相关帮助函数。"""
from flask import request, flash
from utils import CHINA_CITIES
from services.location import compose_location


_LABEL_MAP = {
    'personnel_type': '人员类型', 'employee_id': '员工编号', 'name': '姓名', 'rank': '职级',
    'work_province': '工作所在地-省', 'work_city': '工作所在地-市',
    'household_province': '户口所在地-省', 'household_city': '户口所在地-市',
    'spouse_province': '配偶常住地-省', 'spouse_city': '配偶常住地-市',
    'children_province': '子女常住地-省', 'children_city': '子女常住地-市',
    'has_property': '是否在工作地购置房产',
    'property_delivery_date': '房产交付日期',
    'remote_start_date': '异地开始时间',
}

_PROVINCE_CITY_PAIRS = (
    ('work_province', 'work_city'),
    ('household_province', 'household_city'),
    ('spouse_province', 'spouse_city'),
    ('children_province', 'children_city'),
)


def inject_city_choices(form):
    """提交时根据 POST 的 province 动态填充 city 的 choices，避免 WTForms 报非法选项。"""
    for province_field, city_field in _PROVINCE_CITY_PAIRS:
        province = request.form.get(province_field) or ''
        cities = CHINA_CITIES.get(province, [])
        getattr(form, city_field).choices = [('', '请选择市')] + [(c, c) for c in cities]


def flash_form_errors(form):
    for field, errors in form.errors.items():
        label = _LABEL_MAP.get(field, field)
        for error in errors:
            flash(f'{label}：{error}', 'error')


def validate_personnel_inputs(form, personnel=None, clone_src=None):
    """超出 WTForms 内置能力的必填校验：
      - 户口本材料：新增时必传；编辑时若户口所在地发生调整，必传。
        clone_src 不为空时，将源记录的附件视为已有附件，无需重传。
      - 是否在工作地购置房产 == '是' 时：房产交付日期、是否全部售出、房产材料 均必填。
    返回错误信息列表；为空代表通过。
    """
    errors = []

    new_household_files = [f for f in request.files.getlist('household_file')
                           if f and f.filename]
    existing_household = ([a for a in personnel.attachments if a.category == 'household']
                          if personnel else [])
    if not existing_household and clone_src:
        existing_household = [a for a in clone_src.attachments if a.category == 'household']

    if personnel is None and clone_src is None:
        if not new_household_files:
            errors.append('填写户口所在地后，需上传户口本材料（户口本首页、个人页）')
    elif personnel is not None:
        new_household_loc = compose_location(form.household_province.data,
                                             form.household_city.data)
        if personnel.household_location != new_household_loc:
            if not new_household_files:
                errors.append('调整户口所在地后，需重新上传户口本材料（户口本首页、个人页）')
        elif not existing_household and not new_household_files:
            errors.append('需上传户口本材料（户口本首页、个人页）')
    else:
        # clone 场景：视源记录的户口本为已有，不要求重传
        if not existing_household and not new_household_files:
            errors.append('需上传户口本材料（户口本首页、个人页）')

    if form.has_property.data == '是':
        if not form.property_delivery_date.data:
            errors.append('是否在工作地购置房产为"是"时，房产交付日期必填')
        all_sold = (form.property_all_sold.data or '').strip()
        if not all_sold:
            errors.append('是否在工作地购置房产为"是"时，在工作地购置房产是否全部售出必填')
        new_property_files = [f for f in request.files.getlist('property_contract')
                              if f and f.filename]
        existing_property = ([a for a in personnel.attachments if a.category == 'property']
                             if personnel else [])
        if not existing_property and clone_src:
            existing_property = [a for a in clone_src.attachments if a.category == 'property']
        if not existing_property and not new_property_files:
            errors.append('是否在工作地购置房产为"是"时，需上传房产材料（房产证或购房合同）')

    return errors
