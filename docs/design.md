# CMSS 人员管理系统 — 设计文档

---

## 目录

1. [系统架构](#1-系统架构)
2. [技术栈与依赖](#2-技术栈与依赖)
3. [目录结构](#3-目录结构)
4. [数据库设计](#4-数据库设计)
5. [路由设计](#5-路由设计)
6. [核心业务逻辑](#6-核心业务逻辑)
7. [邮件系统](#7-邮件系统)
8. [定时任务](#8-定时任务)
9. [认证与权限](#9-认证与权限)
10. [配置说明](#10-配置说明)
11. [部署说明](#11-部署说明)

---

## 1. 系统架构

```
┌──────────────────────────────────────────────┐
│                   浏览器                      │
└──────────────┬───────────────────────────────┘
               │ HTTP
┌──────────────▼───────────────────────────────┐
│              Flask 应用层                     │
│                                              │
│  ┌─────────┐  ┌──────────┐  ┌─────────────┐ │
│  │  auth   │  │  public  │  │  personnel  │ │
│  │blueprint│  │blueprint │  │  blueprint  │ │
│  └─────────┘  └──────────┘  └─────────────┘ │
│                                              │
│  ┌──────────┐  ┌──────────┐                 │
│  │ employee │  │  email   │                 │
│  │blueprint │  │blueprint │                 │
│  └──────────┘  └──────────┘                 │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │             服务层 (services/)        │   │
│  │  personnel_service / email_service   │   │
│  │  backup_service / form_helpers       │   │
│  │  location                            │   │
│  └──────────────────────────────────────┘   │
│                                              │
│  ┌──────────────────────────────────────┐   │
│  │         APScheduler（后台线程）        │   │
│  │  邮件定时发送 / 数据备份定时任务        │   │
│  └──────────────────────────────────────┘   │
└──────────────────┬───────────────────────────┘
                   │ SQLAlchemy ORM
┌──────────────────▼───────────────────────────┐
│              SQLite 数据库                    │
│         (personnel.db / instance/)           │
└──────────────────────────────────────────────┘
```

**设计原则**：
- Blueprint 按业务模块拆分，每个 Blueprint 自包含路由和视图逻辑。
- 服务层（`services/`）承载纯业务逻辑，Blueprint 只负责 HTTP 层（请求解析、重定向、flash）。
- 定时任务在独立后台线程中运行，通过 Flask app context 访问数据库。

---

## 2. 技术栈与依赖

| 组件 | 库 | 版本 | 用途 |
|-----|----|-----|------|
| Web 框架 | Flask | 2.0.1 | HTTP 路由、模板渲染、Session |
| ORM | Flask-SQLAlchemy | 2.5.1 | 数据库模型与查询 |
| 表单验证 | Flask-WTF | 0.14.3 | 表单定义、CSRF 保护 |
| 数据处理 | pandas | 1.3.3 | Excel/CSV 导入导出 |
| 数据处理 | openpyxl | 3.0.9 | Excel 文件读写 |
| 拼音转换 | pypinyin | ≥0.50 | 姓名 → 拼音邮箱 |
| 定时任务 | APScheduler | ≥3.10 | 邮件和备份定时任务 |
| 生产服务器 | gunicorn | ≥21.2 | WSGI 生产部署（Linux） |
| 数据库 | SQLite | — | 内置，无需额外安装 |
| 前端 | Bootstrap 5.3 | CDN | 响应式 UI |
| 富文本编辑器 | Quill.js | 1.3.7 | 邮件模板正文编辑（CDN） |

---

## 3. 目录结构

```
cmss_people/
│
├── main.py                   # 应用入口，创建 Flask app
├── config.py                 # 配置类，宏定义
├── models.py                 # SQLAlchemy 数据模型（8 张表）
├── forms.py                  # WTForms 表单定义
├── scheduler.py              # APScheduler 集成
├── utils.py                  # 工具函数（中国省市数据）
├── wsgi.py                   # gunicorn 入口
├── requirements.txt
│
├── blueprints/               # 路由层（按业务模块）
│   ├── __init__.py           # blueprint 注册
│   ├── auth.py               # 认证（登录/登出）
│   ├── public.py             # 公开问卷、首页
│   ├── personnel.py          # 异地记录（列表、审核、导入导出等）
│   ├── employee.py           # 员工管理
│   └── email.py              # 邮件模板、发送、定时、备份
│
├── services/                 # 业务逻辑层
│   ├── __init__.py
│   ├── email_service.py      # 邮件发送、宏替换、模板渲染
│   ├── personnel_service.py  # 人员记录保存、问卷提交、地点拆分
│   ├── backup_service.py     # 数据备份（Excel + SQLite）
│   ├── form_helpers.py       # 表单校验辅助
│   └── location.py           # 地点字符串工具
│
├── templates/                # Jinja2 模板
│   ├── base.html             # 布局基模板（侧边栏导航）
│   ├── login.html
│   ├── index.html            # 首页仪表板
│   │
│   ├── _personnel_form_body.html    # 人员表单（共享片段）
│   ├── _personnel_form_css.html
│   ├── _personnel_form_scripts.html
│   ├── list.html / add.html / edit.html
│   ├── review.html / history.html / trash.html
│   ├── import.html
│   │
│   ├── employees.html        # 员工管理
│   │
│   ├── generate_form.html    # 生成问卷链接
│   ├── external_form.html    # 公开问卷
│   ├── form_submitted.html   # 问卷提交结果
│   │
│   ├── email_list.html / email_edit.html
│   ├── email_config.html / email_send.html
│   ├── email_schedules.html / email_schedule_edit.html
│   ├── email_logs.html / email_backup.html
│
├── docs/                     # 文档目录
│   └── 设计文档.md
│
└── deploy/                   # 部署相关配置
```

---

## 4. 数据库设计

数据库使用 SQLite，由 SQLAlchemy 管理，应用启动时自动 `create_all()`。

### 4.1 Personnel（异地记录核心表）

```sql
CREATE TABLE personnel (
    id                  INTEGER PRIMARY KEY,
    personnel_type      VARCHAR(50)  NOT NULL,  -- 人员类型（5种）
    employee_id         VARCHAR(20)  NOT NULL,  -- 员工编号
    name                VARCHAR(50)  NOT NULL,
    rank                INTEGER,                -- 职级（可为空，历史数据兼容）
    work_location       VARCHAR(100) NOT NULL,  -- 工作所在地（省+市，如"广东省广州市"）
    household_location  VARCHAR(100) NOT NULL,  -- 户口所在地
    spouse_location     VARCHAR(100),           -- 配偶常住地
    children_location   VARCHAR(100),           -- 子女常住地
    has_property        VARCHAR(10)  NOT NULL,  -- '是' | '否'
    property_delivery_date DATE,                -- 房产交付日期
    property_all_sold   VARCHAR(10),            -- '是' | '否' | NULL
    transition_end_date DATE,                   -- 过渡期截止（= 交付日 + 1年 - 1天）
    remote_start_date   DATE,                   -- 符合异地条件的起始日期
    remote_end_date     DATE,                   -- 不再符合异地条件的日期
    work_location_date  DATE,                   -- 工作地变更日期
    household_location_date DATE,               -- 户口地变更日期
    spouse_location_date DATE,                  -- 配偶地变更日期
    is_no_change        BOOLEAN DEFAULT FALSE,  -- 问卷无地点变更但房产信息变化
    notes               TEXT,                   -- 管理员备注
    status              VARCHAR(20) DEFAULT 'pending',
    --   pending   → 待审核
    --   approved  → 已审核（在记录列表显示）
    --   rejected  → 已拒绝（在历史记录显示）
    --   deleted   → 已删除（在历史记录显示）
    created_at          DATETIME,
    updated_at          DATETIME
);
```

**`is_remote_qualified` 计算属性**（Python property，不存储）：
```
工作地 ≠ 户口地
AND（配偶地为空 OR 配偶地 ≠ 工作地）
```

### 4.2 Employee（员工基本信息表）

```sql
CREATE TABLE employee (
    employee_id VARCHAR(20) PRIMARY KEY,
    name        VARCHAR(50) NOT NULL,
    email       VARCHAR(100),         -- 可为空
    created_at  DATETIME
);
```

由 `ensure_employee()` 在审核通过时自动创建，也可手动管理。

### 4.3 Attachment（附件表）

```sql
CREATE TABLE attachment (
    id               INTEGER PRIMARY KEY,
    filename         VARCHAR(255) NOT NULL,
    content_type     VARCHAR(100) NOT NULL,
    data             BLOB NOT NULL,              -- 二进制内容直接存入 DB
    personnel_id     INTEGER REFERENCES personnel(id),  -- 人员记录附件
    email_template_id INTEGER REFERENCES email_template(id), -- 邮件模板附件
    category         VARCHAR(20),  -- 'property' | 'household' | NULL（邮件附件）
    created_at       DATETIME
);
```

附件直接以 BLOB 存于数据库，通过 `/file/<file_id>` 路由流式输出。

### 4.4 FormToken（一次性问卷令牌）

```sql
CREATE TABLE form_token (
    id           INTEGER PRIMARY KEY,
    token        VARCHAR(100) UNIQUE NOT NULL,  -- UUID hex
    is_used      BOOLEAN DEFAULT FALSE,
    created_at   DATETIME,
    employee_id  VARCHAR(20),
    prefill_data JSON   -- build_prefill_for_employee() 快照，避免时序问题
);
```

生成链接时立即保存 prefill 快照，防止问卷打开期间员工数据发生变化导致预填不一致。

### 4.5 PersonnelHistory（审计日志）

```sql
CREATE TABLE personnel_history (
    id            INTEGER PRIMARY KEY,
    personnel_id  INTEGER,              -- 关联 Personnel（可为空，允许记录被删除后仍保留历史）
    history_type  VARCHAR(20),         -- 'insert' 等
    data          JSON,                -- personnel_snapshot() 的完整快照
    should_import BOOLEAN DEFAULT TRUE,
    created_at    DATETIME
);
```

### 4.6 Trash（垃圾桶）

```sql
CREATE TABLE trash (
    id           INTEGER PRIMARY KEY,
    personnel_id INTEGER NOT NULL,
    data         JSON NOT NULL,   -- personnel_snapshot()
    deleted_at   DATETIME
);
```

Personnel 永久删除时，先保存快照到 Trash，再删除 Personnel 行。从 Trash 恢复时重建 Personnel 对象。

### 4.7 EmailTemplate / EmailConfig / EmailLog / EmailSchedule / BackupConfig

详见 [邮件系统](#7-邮件系统) 章节，字段定义参考 `models.py`。

### 4.8 ER 关系图（简化）

```
Employee ──── (1:N, via employee_id) ──── Personnel
Personnel ─── (1:N) ─── Attachment (category=property|household)
EmailTemplate ─── (1:N) ─── Attachment (category=NULL)
FormToken ─── (N:1, via employee_id) ─── Employee
Personnel ─── (1:N) ─── PersonnelHistory
Personnel ─── (1:1, via snapshot) ─── Trash
EmailSchedule ─── (N:1) ─── EmailTemplate
EmailLog ─── (N:1, nullable) ─── EmailSchedule
```

---

## 5. 路由设计

### 5.1 认证（auth blueprint，无前缀）

| 路由 | 方法 | 说明 |
|-----|------|------|
| `/login` | GET, POST | 登录 |
| `/logout` | POST | 登出（清空 session） |

**全局 Hook**：`before_request` 调用 `require_login()`，未登录且非公开端点则 302 到 `/login?next=...`。

**公开端点白名单**：
```python
PUBLIC_ENDPOINTS = {'auth.login', 'public.external_form', 'static'}
```

### 5.2 公开问卷（public blueprint，无前缀）

| 路由 | 方法 | 说明 |
|-----|------|------|
| `/` | GET | 首页仪表板（统计数） |
| `/generate-form` | GET, POST | 生成问卷链接 |
| `/form/<token>` | GET, POST | 公开问卷（无需登录） |
| `/file/<file_id>` | GET | 下载附件 |

### 5.3 异地记录（personnel blueprint，无前缀）

| 路由 | 方法 | 说明 |
|-----|------|------|
| `/list` | GET | 已审核记录列表 |
| `/list/export` | GET | 导出 CSV / Excel |
| `/list/bulk_delete` | POST | 批量逻辑删除（`ids=1,2,3`） |
| `/list/bulk_download` | POST | 批量下载附件 ZIP |
| `/add` | GET, POST | 新增记录（支持 clone） |
| `/edit/<id>` | GET, POST | 编辑记录 |
| `/clone/<id>` | GET | 预填克隆表单 |
| `/delete/<id>` | POST | 逻辑删除 |
| `/review` | GET | 待审核列表 |
| `/approve/<id>` | POST | 通过 |
| `/reject/<id>` | POST | 拒绝 |
| `/review/bulk` | POST | 批量通过 / 拒绝 |
| `/review/approve-all` | POST | 一键全部通过 |
| `/history` | GET | 历史记录（rejected / deleted） |
| `/history/restore/<id>` | POST | 恢复到 approved |
| `/history/delete/<id>` | POST | 永久删除（移至 Trash） |
| `/trash` | GET | 垃圾桶列表 |
| `/trash/restore/<id>` | POST | 从 Trash 恢复 |
| `/trash/delete/<id>` | POST | 彻底删除 |
| `/import` | GET, POST | 数据导入 |
| `/attachment/<id>/delete` | POST | 删除单个附件（支持 AJAX） |

### 5.4 员工管理（employee blueprint，前缀 `/employees`）

| 路由 | 方法 | 说明 |
|-----|------|------|
| `/employees/` | GET | 员工列表 |
| `/employees/add` | POST | 添加员工 |
| `/employees/edit/<id>` | POST | 编辑员工 |
| `/employees/delete/<id>` | POST | 删除员工 |
| `/employees/bulk_delete` | POST | 批量删除 |
| `/employees/fill_default_email` | POST | 批量填充拼音邮箱 |
| `/employees/export` | GET | 导出（`?format=csv\|xlsx`） |
| `/employees/import` | POST | 导入 |
| `/employees/api/search` | GET | 搜索 API（`?q=关键词`），返回 JSON |

### 5.5 邮件功能（email blueprint，前缀 `/email`）

| 路由 | 方法 | 说明 |
|-----|------|------|
| `/email/templates` | GET | 模板列表 |
| `/email/templates/new` | GET, POST | 创建模板 |
| `/email/templates/<id>/edit` | GET, POST | 编辑模板 |
| `/email/templates/<id>/delete` | POST | 删除模板 |
| `/email/config` | GET, POST | SMTP 配置 |
| `/email/send` | GET, POST | 即时发送 |
| `/email/schedules` | GET | 定时任务列表 |
| `/email/schedules/new` | GET, POST | 创建任务 |
| `/email/schedules/<id>/edit` | GET, POST | 编辑任务 |
| `/email/schedules/<id>/delete` | POST | 删除任务 |
| `/email/schedules/<id>/toggle` | POST | 启用 / 禁用 |
| `/email/schedules/<id>/run-now` | POST | 立即执行 |
| `/email/logs` | GET | 发送记录 |
| `/email/backup` | GET, POST | 备份配置 |
| `/email/backup/run-now` | POST | 立即备份 |

---

## 6. 核心业务逻辑

### 6.1 异地条件判断

```python
# models.py - Personnel.is_remote_qualified
@property
def is_remote_qualified(self):
    cond1 = (bool(self.work_location) and bool(self.household_location)
             and self.work_location != self.household_location)
    cond2 = (not self.spouse_location) or (self.spouse_location != self.work_location)
    return cond1 and cond2
```

判断逻辑：
- 条件1：工作地和户口地均不为空，且两者不相同。
- 条件2：配偶地为空，或配偶地不等于工作地（即配偶未住在工作地）。
- 两个条件同时满足则符合异地条件。

### 6.2 过渡期截止计算

```python
# services/personnel_service.py
def calc_transition_end(property_delivery_date):
    """过渡期截止 = 房产交付日期 + 1年 - 1天"""
    if not property_delivery_date:
        return None
    d = property_delivery_date
    try:
        next_year = d.replace(year=d.year + 1)
    except ValueError:
        # 2月29日的闰年处理：次年改为2月28日
        next_year = d.replace(year=d.year + 1, day=28)
    return next_year - timedelta(days=1)
```

### 6.3 地点拆分逻辑（问卷提交核心）

当员工通过问卷更新地点信息时，若本次提交与最新已审核记录相比存在地点变更，系统按变更时间自动拆分为多条 `pending` 记录。

```
调用入口：services/personnel_service.py - save_prefill_submission()

输入：
  form       - 问卷提交的表单数据（含各地点及变更时间）
  prefill    - 生成链接时保存的历史快照

处理流程：
  1. 查询员工最新 approved Personnel（base 记录）
  2. 比对三个地点字段（工作地、户口地、配偶地）
  3. 对每个发生变更的地点，记录（地点字段, 变更日期, 新值）
  4. 如果没有任何地点变更：
     - 若房产信息（has_property / property_all_sold / property_delivery_date）有变化
       → 生成一条 is_no_change=True 的 pending 记录
     - 否则不生成记录（返回空列表）
  5. 如果有地点变更：
     a. 按变更日期升序排序
     b. 从 base 记录出发，逐步"滚动"累积地点变化
     c. 每个变更时间点生成一条 pending 记录：
        - 记录该时间点的地点组合
        - 调用 is_remote_qualified() 判断该组合是否符合异地条件
        - 符合 → remote_start_date = 变更日期，remote_end_date = NULL
        - 不符合 → remote_end_date = 变更日期，remote_start_date = NULL
```

**示例**：

```
原记录：工作地=北京，户口地=上海，配偶地=无

本次提交：
  工作地=广州，变更时间=2024-03-01
  户口地=深圳，变更时间=2024-02-01
  配偶地=无（未变更）

拆分结果（按日期升序）：

Step 1: 2024-02-01，户口地变更为深圳
  → 当前组合：工作=北京，户口=深圳，配偶=无
  → is_remote_qualified: 北京 ≠ 深圳 → True
  → 生成 pending: remote_start_date=2024-02-01

Step 2: 2024-03-01，工作地变更为广州
  → 当前组合：工作=广州，户口=深圳，配偶=无
  → is_remote_qualified: 广州 ≠ 深圳 → True
  → 生成 pending: remote_start_date=2024-03-01
```

### 6.4 地点字符串处理

```python
# services/location.py

def compose_location(province, city):
    """省 + 市 → 地点字符串"""
    if not province and not city:
        return None
    if province == city:        # 直辖市（北京市 + 北京市 → 北京市）
        return province
    return (province or '') + (city or '')

def split_location(location):
    """地点字符串 → (省, 市)"""
    # 遍历 CHINA_CITIES 字典正向匹配
    # 支持省+市（"广东省广州市"）或仅市（"广州市"）
    # 返回 (province, city) 或 ('', '') 
```

### 6.5 附件校验逻辑

```python
# services/form_helpers.py - validate_personnel_inputs()
```

**户口本材料校验**：
- 新增记录：必须上传户口本文件。
- 编辑记录：
  - 若户口所在地（省市）与原记录相同且已有附件：无需重传。
  - 若户口所在地发生变更：必须重传户口本。
  - 若原记录无附件：必须上传。
- 克隆记录：源记录附件可被继承，视为已上传（但可手动移除）。

**房产材料校验**（当 `has_property = '是'` 时）：
- 房产交付日期：必填。
- 是否全部售出：必填。
- 房产材料（房产证或购房合同）：已有附件或本次上传，二者满足其一即可。

### 6.6 数据导入的宽松日期解析

```python
# blueprints/personnel.py - _parse_date()

_DATE_FORMATS = (
    '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d',
    '%Y年%m月%d日', '%Y年%m月%d',
    '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y', '%m-%d-%Y',
)

def _parse_date(s):
    # 1. 标准化分隔符和空格
    # 2. 截取 Excel datetime 字符串的日期部分（"2024-05-15 00:00:00"）
    # 3. 逐格式尝试 strptime
    # 4. 正则兜底：抽取年/月/日三段数字
    # 5. pd.to_datetime 兜底
```

---

## 7. 邮件系统

### 7.1 宏体系

邮件模板通过 `{{宏名称}}` 进行个性化替换，分三层：

```
EMAIL_MACROS（发送邮件时使用）
├── 基础宏（来源：Employee 表）
│   ├── {{姓名}}
│   ├── {{员工编号}}
│   ├── {{邮箱}}
│   └── {{问卷链接}}     ← 特殊：每人唯一，触发 FormToken 生成
└── PERSONNEL_MACROS（来源：最新 approved Personnel）
    ├── {{人员类型}}
    ├── {{职级}}
    ├── {{工作所在地}} / {{工作所在地时间}}
    ├── {{户口所在地}} / {{户口所在地时间}}
    ├── {{配偶常住地}} / {{配偶常住地时间}}
    ├── {{子女常住地}}
    ├── {{是否在工作地购置房产}}
    ├── {{房产交付日期}} / {{在工作地购置房产是否全部售出}}
    ├── {{过渡期截止}}
    ├── {{异地开始时间}} / {{异地结束时间}}
    ├── {{是否符合异地条件}}
    └── {{备注}}
```

### 7.2 宏替换实现

```python
# services/email_service.py - render_template_for(tpl, employee)

def render_template_for(tpl, employee):
    # 1. 基础宏替换（姓名、员工编号、邮箱）
    subject = tpl.subject.replace('{{姓名}}', employee.name or '')...
    body    = tpl.body.replace('{{姓名}}', employee.name or '')...

    # 2. {{问卷链接}} 特殊处理
    #    - 生成新 FormToken（_issue_survey_token）
    #    - 在 subject 中替换为纯 URL
    #    - 在 body 中替换为 <a href="URL">URL</a>（HTML 转义后注入）

    # 3. Personnel 宏替换
    #    - 查询员工最新 approved Personnel
    #    - 17 个字段逐一替换，无记录时替换为空字符串

    return subject, body
```

### 7.3 SMTP 发送

```python
# services/email_service.py - _send_one()

def _send_one(cfg, subj, body, to, cc, bcc, attachments):
    # 1. 构建 MIMEMultipart('alternative') 邮件对象
    # 2. 添加 HTML 正文
    # 3. 遍历附件，使用 RFC2231 编码文件名（解决中文文件名乱码）
    # 4. 建立 SMTP 连接（SSL / STARTTLS）
    # 5. 发送，关闭连接
```

**错误处理**：`_friendly_smtp_error()` 将底层 SMTP 异常翻译为可读的中文提示。

### 7.4 定时任务收件人解析

```python
# services/email_service.py - resolve_schedule_employees(sched)

# recipient_mode = 'ids'
→ 直接按 employee_id 列表查询 Employee

# recipient_mode = 'filter'
→ 查询满足以下条件的最新 Personnel：
  - rank BETWEEN rank_min AND rank_max（若配置）
  - is_remote_qualified 满足 remote 条件
  - remote_start_date BETWEEN remote_from AND remote_to（若配置）
  → 提取 employee_id 去重 → 查询对应 Employee
```

### 7.5 数据备份

```python
# services/backup_service.py

def run_backup(cfg):
    # 1. build_personnel_excel() → pandas DataFrame → Excel 字节流
    # 2. build_sqlite_backup() → SQLite Online Backup API → 字节流
    #    db.backup(backup_db) 是线程安全的，不影响正在运行的写操作
    # 3. 构建邮件（MIMEMultipart），两个附件
    # 4. 使用 EmailConfig.get_active() 获取 SMTP 配置
    # 5. 发送，写 EmailLog，更新 BackupConfig.last_run_at / last_status
```

---

## 8. 定时任务

### 8.1 调度器架构

```python
# scheduler.py
_scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
```

使用 APScheduler 的 `BackgroundScheduler`，在独立后台线程中运行，与 Flask 主线程不共享 HTTP 上下文。所有定时任务回调函数通过 `with app.app_context()` 手动建立 Flask 上下文后访问数据库。

**任务 ID 规范**：
- 邮件任务：`email_schedule_{sched.id}`
- 备份任务：`backup_schedule`

### 8.2 触发器类型

| 类型 | APScheduler 触发器 | 示例 |
|-----|-----------------|------|
| `daily` | `CronTrigger(hour=h, minute=m)` | 每天 09:00 |
| `monthly` | `CronTrigger(day=d, hour=h, minute=m)` | 每月1日 09:00 |
| `once` | `DateTrigger(run_date=dt)` | 2024-06-01 09:00 |
| 备份-`weekly` | `CronTrigger(day_of_week=w, hour=h, minute=m)` | 每周一 02:00 |

所有触发器的 `misfire_grace_time=3600`：若任务因服务重启等原因错过执行时间，在 1 小时内仍会补跑一次。

### 8.3 初始化与重载

```python
# 应用启动时
init_scheduler(app)
  └── reload_all_jobs()
        ├── 清除所有 email_schedule_* 和 backup_schedule 任务
        ├── 为所有 enabled=True 的 EmailSchedule 重新注册任务
        └── 若 BackupConfig.enabled=True 则注册备份任务

# 修改任务后
add_or_update_job(sched)  # 更新或重新注册
remove_job(sched_id)      # 删除任务
refresh_backup_job(cfg)   # 备份任务更新
```

---

## 9. 认证与权限

### 9.1 认证机制

- 基于 Flask `session`（服务端 Cookie，由 `SECRET_KEY` 签名）。
- 账户存储在 `main.py` 的 `USERS` 字典中，明文配置（无注册入口，无密码哈希）。
- CSRF 保护：全局启用 `CSRFProtect`，所有 POST 表单需携带 `csrf_token`。

### 9.2 访问控制

```python
# blueprints/auth.py
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return  # 放行
    if 'user' not in session:
        return redirect(url_for('auth.login', next=request.url))
```

公开端点（无需登录）：
- `/login` — 登录页
- `/form/<token>` — 员工问卷
- 静态资源

所有其他路由均需登录后访问。

### 9.3 安全注意事项

- 生产环境必须修改 `SECRET_KEY`（默认 `dev-change-me` 不安全）。
- 生产环境必须修改 `USERS` 中的默认密码。
- 若需更强的认证，可替换为数据库账户 + `werkzeug.security.generate_password_hash`。

---

## 10. 配置说明

### 10.1 config.py

```python
class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///personnel.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SCHEDULER_TIMEZONE = 'Asia/Shanghai'

    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '')
    # 用途：定时发送邮件时，任务在后台线程运行，无法通过 request.host 获取域名。
    # 配置示例：PUBLIC_BASE_URL=https://hr.example.com
    # 若不配置，系统尝试用 url_for(_external=True) 或 SERVER_NAME 推断。

    PROVIDER_PRESETS = {
        'qq':   {'smtp_server': 'smtp.qq.com',          'smtp_port': 465, 'use_ssl': True},
        'cmss': {'smtp_server': 'smtp.chinamobile.com', 'smtp_port': 465, 'use_ssl': True},
    }
```

### 10.2 环境变量

| 变量 | 说明 | 默认值 |
|-----|------|-------|
| `FLASK_SECRET_KEY` | Session 签名密钥 | `dev-change-me` |
| `DATABASE_URL` | SQLite 路径 | `sqlite:///personnel.db` |
| `PUBLIC_BASE_URL` | 系统公网地址 | `""` |

### 10.3 数据库路径

- 默认存储在项目根目录 `personnel.db`。
- 也可能在 `instance/personnel.db`（Flask instance 目录），取决于运行方式。
- 可通过 `DATABASE_URL=sqlite:////absolute/path/to/personnel.db` 指定绝对路径。

---

## 11. 部署说明

### 11.1 开发环境

```bash
pip install -r requirements.txt
python main.py
# 访问 http://localhost:5000
```

### 11.2 生产环境（Linux + gunicorn）

```bash
pip install -r requirements.txt

# 配置环境变量
export FLASK_SECRET_KEY='your-strong-secret-key-here'
export DATABASE_URL='sqlite:////var/data/personnel.db'
export PUBLIC_BASE_URL='https://hr.example.com'

# 启动
gunicorn main:app -b 0.0.0.0:5000 -w 1
```

> **注意**：APScheduler 的 `BackgroundScheduler` 与多 worker 进程不兼容。gunicorn 必须使用 `-w 1`（单 worker）。若需要多进程，需换用分布式任务队列（如 Celery + Redis）。

### 11.3 Windows 环境

```bat
set FLASK_SECRET_KEY=your-strong-secret-key-here
set PUBLIC_BASE_URL=http://your-server-ip:5000
python main.py
```

### 11.4 注意事项

| 项目 | 说明 |
|-----|------|
| gunicorn worker 数 | 必须为 1（APScheduler 限制） |
| 数据库备份 | 除系统自带邮件备份外，建议额外对 `personnel.db` 做文件级备份 |
| 附件存储 | 附件以 BLOB 存入 SQLite，数据库文件会随附件增多而增大 |
| 时区 | 所有时间以北京时间（UTC+8）存储，通过 `beijing_now()` 生成 |

### 11.5 首次部署

应用启动时自动执行 `db.create_all()`，所有表结构自动创建，无需手动执行 SQL。

若需要预置员工数据，可：
1. 启动应用后，通过「员工管理 → 导入」上传员工 Excel。
2. 或通过「数据导入 → 全量覆盖」导入历史异地记录。
