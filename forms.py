#!/usr/bin/env python3
"""
表单文件
"""
from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, SelectField, DateField, SubmitField, HiddenField, FileField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional
from utils import generate_province_options, get_cities_by_province


class OptionalDateField(DateField):
    """空字符串等价于未填写（WTForms 3.x 默认把空串视为非法日期）。"""
    def process_formdata(self, valuelist):
        if valuelist and (not valuelist[0] or not valuelist[0].strip()):
            self.data = None
            return
        super().process_formdata(valuelist)

class PersonnelForm(FlaskForm):
    """人员信息表单"""
    personnel_type = SelectField('人员类型', choices=[
        ('中层管理人员', '中层管理人员'),
        ('中层管理人员兼省代表', '中层管理人员兼省代表'),
        ('13职级及以上专家岗位人员', '13职级及以上专家岗位人员'),
        ('省代表', '省代表'),
        ('站点总监', '站点总监')
    ], validators=[DataRequired()])
    employee_id = StringField('员工编号', validators=[DataRequired(), Length(max=20)], filters=[lambda s: s.strip() if s else s])
    name = StringField('姓名', validators=[DataRequired(), Length(max=50)], filters=[lambda s: s.strip() if s else s])
    rank = IntegerField('职级', validators=[DataRequired()])
    rank_date = OptionalDateField('职级调整时间', format='%Y-%m-%d')
    # 工作所在地
    work_province = SelectField('工作所在地-省', choices=[('', '请选择省')] + generate_province_options(), validators=[DataRequired()])
    work_city = SelectField('工作所在地-市', choices=[('', '请选择市')], validators=[DataRequired()])
    work_location = HiddenField()
    
    # 户口所在地
    household_province = SelectField('户口所在地-省', choices=[('', '请选择省')] + generate_province_options(), validators=[DataRequired()])
    household_city = SelectField('户口所在地-市', choices=[('', '请选择市')], validators=[DataRequired()])
    household_location = HiddenField()
    
    # 配偶常住地（选填）
    spouse_province = SelectField('配偶常住地-省', choices=[('', '请选择省')] + generate_province_options(), validate_choice=False)
    spouse_city = SelectField('配偶常住地-市', choices=[('', '请选择市')], validate_choice=False)
    spouse_location = HiddenField()

    # 子女常住地（选填）
    children_province = SelectField('子女常住地-省', choices=[('', '请选择省')] + generate_province_options(), validate_choice=False)
    children_city = SelectField('子女常住地-市', choices=[('', '请选择市')], validate_choice=False)
    children_location = HiddenField()
    has_property = SelectField('是否在工作地购置房产', choices=[
        ('是', '是'),
        ('否', '否')
    ], validators=[DataRequired()])
    property_delivery_date = OptionalDateField('房产交付日期', format='%Y-%m-%d')
    property_all_sold = SelectField('在工作地购置房产是否全部售出', choices=[
        ('', '请选择'),
        ('是', '是'),
        ('否', '否'),
    ], validators=[Optional()], validate_choice=False)
    property_contract = FileField('房产证或购房合同')
    remote_start_date = OptionalDateField('异地开始时间', format='%Y-%m-%d')
    remote_end_date = OptionalDateField('异地结束时间', format='%Y-%m-%d')
    work_location_date = OptionalDateField('工作所在地时间', format='%Y-%m-%d')
    household_location_date = OptionalDateField('户口所在地时间', format='%Y-%m-%d')
    spouse_location_date = OptionalDateField('配偶常住地时间', format='%Y-%m-%d')
    notes = TextAreaField('备注', validators=[Optional()], filters=[lambda s: s.strip() if s else s])
    created_at_edit = OptionalDateField('创建时间', format='%Y-%m-%d')
    submit = SubmitField('提交')