# systemd 部署指引

## 1. 两种启动方式

本项目现在同时支持以下两种启动方式：

| 场景 | 命令 | 说明 |
|------|------|------|
| **开发/本地调试** | `python main.py` | Flask dev server，支持热重载（默认关闭） |
| **生产部署** | `gunicorn -w 1 -b 0.0.0.0:5000 wsgi:app` | 单 worker，经 systemd 托管 |

> ⚠️ **必须 `-w 1`（单 worker）**。APScheduler 的 BackgroundScheduler 内嵌在 Python 进程中，多 worker 会导致同一个定时任务被重复触发。

## 2. 首次部署步骤（Linux）

```bash
# 2.1 部署代码
sudo mkdir -p /opt/cmss_people
sudo chown www-data:www-data /opt/cmss_people
# 把项目拷到 /opt/cmss_people（git clone / rsync / scp 均可）

# 2.2 建虚拟环境 & 装依赖
cd /opt/cmss_people
sudo -u www-data python3 -m venv .venv
sudo -u www-data .venv/bin/pip install -r requirements.txt

# 2.3 复制 systemd 单元文件
sudo cp deploy/cmss-people.service /etc/systemd/system/

# 2.4 按实际情况编辑（路径 / SECRET_KEY / 运行账号）
sudo vim /etc/systemd/system/cmss-people.service

# 2.5 加载并启用
sudo systemctl daemon-reload
sudo systemctl enable cmss-people    # 开机自启
sudo systemctl start  cmss-people    # 立即启动
```

## 3. 常用 systemctl 命令

```bash
sudo systemctl start   cmss-people   # 启动
sudo systemctl stop    cmss-people   # 停止
sudo systemctl restart cmss-people   # 重启（改完代码后）
sudo systemctl status  cmss-people   # 查看状态
sudo systemctl enable  cmss-people   # 开机自启
sudo systemctl disable cmss-people   # 取消开机自启

# 查看日志（实时跟踪）
sudo journalctl -u cmss-people -f

# 查看最近 100 行日志
sudo journalctl -u cmss-people -n 100
```

## 4. 更新代码后

```bash
cd /opt/cmss_people
git pull                              # 拉最新代码
.venv/bin/pip install -r requirements.txt   # 依赖若有变化
sudo systemctl restart cmss-people    # 重启服务
```

## 5. 修改配置项

配置通过**环境变量**注入（见 `.service` 里的 `Environment=` 行）：

- `FLASK_SECRET_KEY` — Flask 会话密钥（务必改成随机长串）
- `DATABASE_URL` — 数据库地址，默认 `sqlite:///personnel.db`

改完 `.service` 文件后：

```bash
sudo systemctl daemon-reload
sudo systemctl restart cmss-people
```

## 6. 反向代理（可选）

建议前置 nginx 处理 HTTPS 与静态资源：

```nginx
server {
    listen 80;
    server_name cmss.example.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
