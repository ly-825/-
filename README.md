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

注意：

```text
.env 保存本地真实密钥
.env.example 只保留示例配置，不要填写真实密钥
```

### 4. 启动服务

```bash
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

如果要用真手机在同一 Wi-Fi 下测试小程序，需要让后端监听局域网：

```bash
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

同时把 `miniprogram/app.js` 中的 `baseUrl` 改成电脑局域网地址，例如：

```text
http://192.168.31.68:8000
```

## 小程序前端

小程序代码位于：

```text
miniprogram/
```

使用方式：

```text
1. 先启动后端服务
2. 打开微信开发者工具
3. 导入 backend/miniprogram 目录
4. 电脑开发者工具调试时可使用 http://127.0.0.1:8000
5. 真手机预览时使用电脑局域网 IP，并在微信开发者工具中勾选“不校验合法域名、web-view、TLS 版本以及 HTTPS 证书”
```

当前小程序覆盖：

```text
工作台
图纸上传、列表、详情确认、重新识别
产品库存查询、产品入库、产品出库、库存流水
待入库余料确认、余料查询、余料出库、余料流水
```

小程序不包含扫码功能。

## 内部试运行

当前版本适合少量人员内部试运行。试运行前建议确认：

```text
1. 后端服务已启动
2. 手机和电脑在同一个 Wi-Fi
3. 小程序 baseUrl 指向当前后端地址
4. /api/mobile/summary 可以访问
5. 已执行一次数据备份
6. 产品入库、产品出库、余料确认、余料出库、流水撤销流程已抽查
```

正式发布前仍建议补充登录权限、正式 HTTPS 域名、服务器部署、自动备份和生产数据库方案。

## 数据备份

库存数据和上传图纸默认保存在：

```text
data/app.db
data/uploads/
```

试运行期间，建议每天使用前或重要操作前执行一次备份：

```bash
bash scripts/backup.sh
```

备份会生成到：

```text
backups/年-月-日_时分秒/
```

恢复时：

```text
1. 停止后端服务
2. 将备份中的 app.db 复制回 data/app.db
3. 将备份中的 uploads 内容复制回 data/uploads/
4. 重新启动后端服务
```

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

### 小程序图纸管理

```http
POST /api/mobile/drawings/upload
GET /api/mobile/drawings
GET /api/mobile/drawings/pending
GET /api/mobile/drawings/confirmed
GET /api/mobile/drawings/{drawing_id}
POST /api/mobile/drawings/{drawing_id}/confirm
POST /api/mobile/drawings/{drawing_id}/rerun
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

- 增加用户和权限
- 增加小程序前端页面
- 增加 MySQL/PostgreSQL 生产配置
- 增加图纸模板规则库
