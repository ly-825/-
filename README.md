# DXF用料识别与余料匹配后端MVP

这是第一版后端 MVP，采用 `FastAPI + ezdxf + Qwen-Plus + SQLite`，用于跑通：

```text
上传DXF → 解析候选信息 → 千问结构化识别 → 人工确认 → 产品入库 → 余料确认入库 → 余料查询/出库
```

## 目录结构

```text
backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── routers/
│   │   ├── drawings.py
│   │   ├── inventory.py
│   │   └── mobile.py
│   └── services/
│       ├── dxf_parser.py
│       ├── inventory_service.py
│       ├── qwen_service.py
│       ├── qr_service.py
│       └── scrap_service.py
├── miniprogram/
│   ├── app.js
│   ├── app.json
│   ├── app.wxss
│   ├── project.config.json
│   ├── utils/
│   │   └── api.js
│   └── pages/
│       ├── index/
│       ├── drawings/
│       ├── inventory/
│       └── scraps/
├── requirements.txt
├── .env.example
└── README.md
```

## 启动步骤

### 1. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

如果要启用千问识别，在 `.env` 中填写：

```text
DASHSCOPE_API_KEY=你的APIKey
```

如果不填 API Key，系统会使用 `ezdxf` 的候选几何信息返回保守结果，并要求人工确认。

### 登录与微信配置

生产运行前需要在 `.env` 中配置以下项目，真实值不得提交到 Git：

```text
PRODUCTION=false
AUTH_PEPPER=
OWNER_TOTP_SECRET=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
PC_SESSION_HOURS=12
MOBILE_SESSION_DAYS=30
```

生成服务端密钥和老板动态验证码密钥：

```bash
openssl rand -hex 32
.venv/bin/python -c 'import pyotp; print(pyotp.random_base32())'
```

将第一条结果填入 `AUTH_PEPPER`，第二条结果填入 `OWNER_TOTP_SECRET`。微信公众平台提供的小程序 AppID 和 AppSecret 分别填入 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`。服务器正式启用 HTTPS 后将 `PRODUCTION` 改为 `true`，此时接口文档会关闭。

首次创建唯一的老板账号：

```bash
.venv/bin/python scripts/create_owner.py --username owner --display-name 老板
```

命令会要求输入至少 12 位密码，并输出一次性的身份验证器配置地址。配置完成后，老板使用账号、密码和六位动态验证码登录 PC 后台。

注意：

```text
.env 保存本地真实密钥
.env.example 只保留示例配置，不要填写真实密钥
```

### 4. 启动服务

```bash
cd /Users/luck/Desktop/杭州特耐时/backend
.venv/bin/python -m uvicorn app.main:app --reload
```

访问：

```text
http://127.0.0.1:8000/docs
```

中文后台：

```text
http://127.0.0.1:8000/admin
```

工厂电脑要持续提供手机访问时，使用下面的固定启动命令。它会监听局域网，不启用开发用的自动重载：

```bash
cd /Users/luck/Desktop/杭州特耐时/backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 微信小程序

小程序代码位于：

```text
miniprogram/
```

使用方式：

```text
1. 后端服务启动并保持运行
2. 私有测试阶段通过后台连接页配置测试地址
3. 小程序打开后先进入员工登录页
4. 首次使用时输入老板在 PC 后台创建的工号和 8 位激活码，完成微信绑定
5. 后续直接微信登录；登录成功后进入“计划”页
```

小程序底部只保留三个业务入口：

```text
计划
材料（钢板、余料、纸材）
成品
```

计划查料、钢板管理和纸材管理已经接入与 PC 后台相同的服务层和 SQLite 数据库。小程序当前支持：

- 计划：筛选已确认图纸，输入计划数量，查看成品、余料、钢板匹配明细和用料建议。
- 钢板：规格新增/修改/启停、按重量入库、分组库存、批次修改、FIFO 出库、流水查询和撤销。
- 纸材：纸圈/纸张规格新增/修改/启停、批次入库、分组库存与批次明细、FIFO 出库、流水查询和撤销。
- 成品和余料：继续使用现有小程序页面与移动接口。

PC 和小程序不做数据复制或延迟同步：任一端提交成功后，另一端刷新页面即可看到相同数据。所有小程序库存写操作都会在提交前逐项确认，并携带持久化的 `client_request_id`；网络响应不确定时必须重试原请求，服务端会返回首次结果而不会重复入库、出库或撤销。同一个请求编号不得改成其他负载。

Excel 导入、导出、操作日志、助手和高级统计仍只在 PC 后台提供，小程序不提供这些入口。

图纸文件、预览、文件哈希和识别原始数据仅老板的 PC 后台可访问。员工小程序选择产品时使用精简产品选项接口，不会获得图纸文件数据。

二维码只包含版本号和局域网连接地址，不包含数据库、业务数据、密码或令牌。工厂路由器或后台电脑 IP 变化后，在电脑后台重新生成二维码并让手机重新扫描；不要修改 `miniprogram/app.js`。

微信开发者工具导入 `backend/miniprogram` 目录。厂内局域网开发阶段需要开启“不校验合法域名、web-view 域名、TLS 版本以及 HTTPS 证书”；真机调试时需要允许小程序访问本地网络。

连接失败时按以下顺序检查：

```text
1. 手机是否连接工厂 Wi-Fi，而不是移动网络或其他 Wi-Fi
2. 后台电脑是否开机并运行 0.0.0.0:8000 服务
3. Windows 防火墙是否允许 8000 端口
4. 电脑 IP 是否变化；如有变化，重新生成并扫描二维码
5. 仍无法连接时，在小程序中使用“手工设置地址”测试 IP 和端口
```

## 内部试运行

当前版本适合少量人员内部试运行。试运行前建议确认：

```text
1. 后端服务已启动
2. 手机和电脑在同一个 Wi-Fi
3. 已通过电脑后台二维码完成小程序连接
4. /api/mobile/summary 可以访问
5. 已执行一次数据备份
6. 计划查料结果已与 PC 页面抽查一致
7. 钢板和纸材的规格、入库、出库、流水、撤销流程已各抽查一次
8. 产品入库、产品出库、余料确认、余料出库、流水撤销流程已抽查
```

正式发布还需要完成 ICP 备案、HTTPS 域名、个人 ECS 部署和自动备份；登录与角色权限已经由后端强制执行。

## 数据备份

库存数据和上传图纸默认保存在：

```text
data/app.db
data/uploads/
```

系统使用 SQLite 在线备份 API 生成一致快照，运行中的服务无需停机，也不会直接拷贝活动数据库文件。手工触发：

```bash
bash scripts/backup.sh
```

备份会生成数据库、上传文件和 SHA-256 清单：

```text
backups/年-月-日_时分秒/
```

默认先做只读验证：

```bash
.venv/bin/python scripts/restore_backup.py backups/年-月-日_时分秒
```

恢复演练应写入新的临时目录，不覆盖当前数据：

```bash
.venv/bin/python scripts/restore_backup.py backups/年-月-日_时分秒 --target /tmp/tenaishi-restore-test
```

个人 ECS 本地备份目录为 `/srv/tenaishi/backups`，保留 7 天；阿里云 ECS 文件备份同时保护 `/srv/tenaishi/backups` 和 `/srv/tenaishi/data/uploads`，云端保留 30 天。

后台包含：

```text
后台首页
图纸管理：图纸识别、待确认图纸、已确认图纸
库存管理：库存查询、产品入库、产品出库、库存流水
余料管理：待入库余料、余料记录、余料出库、余料流水
```

主要业务流程：

```text
上传/识别图纸
→ 人工确认图纸
→ 产品库存入库
→ 按产品入库数量自动生成同数量待入库余料
→ 仓库确认实际尺寸和库位
→ 余料变为可用库存
```

库存和余料分开管理：

```text
库存管理：只管理产品库存
余料管理：只管理切割后产生的余料
```

常用后台入口：

```text
/admin/drawings                图纸识别
/admin/drawings/pending        待确认图纸
/admin/drawings/confirmed      已确认图纸
/admin/inventory               库存查询
/admin/inventory/inbound       产品入库
/admin/inventory/outbound      产品出库
/admin/inventory/transactions  库存流水
/admin/scraps/pending          待入库余料
/admin/scraps                  余料记录
/admin/scraps/outbound         余料出库
/admin/scraps/transactions     余料流水
```

## 初始化测试库存

如需快速测试余料匹配，可执行：

```bash
python -m app.seed
```

会写入两条测试余料：

```text
余料A：50#，2.65厚，圆片φ130
余料B：50#，2.65厚，圆片φ180
```

## 核心接口

### 小程序工作台

```http
GET /api/mobile/summary
```

### 小程序计划查料

```http
GET /api/mobile/plans/drawings
GET /api/mobile/plans/match
```

### 小程序钢板管理

```http
GET  /api/mobile/raw-plate-specifications
POST /api/mobile/raw-plate-specifications
PUT  /api/mobile/raw-plate-specifications/{specification_id}
POST /api/mobile/raw-plate-specifications/{specification_id}/toggle
GET  /api/mobile/raw-plates
GET  /api/mobile/raw-plates/{batch_id}
PUT  /api/mobile/raw-plates/{batch_id}
POST /api/mobile/raw-plates/inbound
POST /api/mobile/raw-plates/outbound
GET  /api/mobile/raw-plates/transactions
POST /api/mobile/raw-plates/transactions/{transaction_id}/reverse
```

### 小程序纸材管理

```http
GET  /api/mobile/paper-specifications
POST /api/mobile/paper-specifications
PUT  /api/mobile/paper-specifications/{specification_id}
POST /api/mobile/paper-specifications/{specification_id}/toggle
GET  /api/mobile/paper-materials
GET  /api/mobile/paper-materials/{specification_id}/batches
POST /api/mobile/paper-materials/inbound
POST /api/mobile/paper-materials/outbound
GET  /api/mobile/paper-materials/transactions
POST /api/mobile/paper-materials/transactions/{transaction_id}/reverse
```

上述 `POST`/`PUT` 写接口都要求请求体包含唯一的 `client_request_id`。同编号、同负载可安全重试；同编号、不同负载会返回 `409`，此时应先核对原操作结果，不能生成新负载覆盖原请求。

### 老板图纸接口（仅 PC 老板会话）

```http
POST /api/mobile/drawings/upload
GET /api/mobile/drawings
GET /api/mobile/drawings/pending
GET /api/mobile/drawings/confirmed
GET /api/mobile/drawings/{drawing_id}
POST /api/mobile/drawings/{drawing_id}/confirm
POST /api/mobile/drawings/{drawing_id}/rerun
```

员工小程序不能调用以上接口。员工入库、出库选择产品时使用：

```http
GET /api/mobile/product-options
```

### 小程序产品库存

```http
GET /api/mobile/products
GET /api/mobile/products/{product_code}/batches
POST /api/mobile/products/inbound
POST /api/mobile/products/outbound
GET /api/mobile/products/transactions
```

### 小程序余料管理

```http
GET /api/mobile/scraps/pending
POST /api/mobile/scraps/{inventory_id}/confirm
GET /api/mobile/scraps
POST /api/mobile/scraps/outbound
GET /api/mobile/scraps/transactions
```

### 上传DXF

```http
POST /api/drawings/upload
```

### 确认解析结果

```http
POST /api/drawings/{drawing_id}/confirm
```

### 新增库存

```http
POST /api/inventory
```

## 余料匹配规则

当前第一版规则：

- 余料状态必须是 `available`
- 余料数量必须大于 `0`
- 材质必须完全一致
- 厚度误差不超过 `THICKNESS_TOLERANCE`
- 尺寸必须满足产品需求加 `MACHINING_MARGIN`
- 浪费面积越小越靠前

## 千问接入策略

系统不会把完整 DXF 文件发给大模型，而是先用 `ezdxf` 提取：

- 文本候选
- 尺寸标注候选
- 圆直径候选
- 外接矩形

再把候选 JSON 发给 Qwen-Plus 做字段归一化。

## 下一步建议

- 完成个人 ECS、HTTPS 域名和 systemd 服务部署
- 配置 SQLite 在线备份、异地保存和定期恢复演练
- 增加图纸模板规则库
