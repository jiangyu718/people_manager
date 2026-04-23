#!/usr/bin/env python3
"""账密登录：首次初始化 / 登录 / 登出 + 全局登录校验。账户存储于数据库。"""
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session,
)
from werkzeug.security import generate_password_hash, check_password_hash


auth_bp = Blueprint('auth', __name__)

# 无需登录即可访问的 endpoint
PUBLIC_ENDPOINTS = {
    'auth.login',
    'auth.setup',
    'public.external_form',
    'static',
}


def _get_credential():
    from models import AppCredential
    return AppCredential.query.first()


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """首次启动初始化账户，已配置后跳首页。"""
    if _get_credential():
        return redirect(url_for('public.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

        if not username:
            flash('用户名不能为空', 'error')
        elif len(password) < 6:
            flash('密码长度至少 6 位', 'error')
        elif password != confirm:
            flash('两次输入的密码不一致', 'error')
        else:
            from models import db, AppCredential
            db.session.add(AppCredential(
                username=username,
                password_hash=generate_password_hash(password),
            ))
            db.session.commit()
            flash('账户设置成功，请登录', 'success')
            return redirect(url_for('auth.login'))

    return render_template('setup.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        cred = _get_credential()
        if cred and username == cred.username and check_password_hash(cred.password_hash, password):
            session.clear()
            session['user'] = username
            next_url = request.args.get('next') or url_for('public.index')
            return redirect(next_url)
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    flash('已退出登录', 'success')
    return redirect(url_for('auth.login'))


def require_login():
    """注册为 before_request：未完成初始化时跳初始化页，未登录时跳登录页。"""
    endpoint = request.endpoint
    if endpoint is None or endpoint == 'static':
        return

    # 尚未配置账户：强制进入初始化页（setup 本身除外）
    if endpoint != 'auth.setup' and not _get_credential():
        return redirect(url_for('auth.setup'))

    if endpoint in PUBLIC_ENDPOINTS:
        return

    if session.get('user'):
        return

    return redirect(url_for('auth.login',
                             next=request.full_path if request.query_string else request.path))
