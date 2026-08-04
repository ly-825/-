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

## 微信小程序（厂内 Wi-Fi）

小程序代码位于：

```text
miniprogram/
```

使用方式：

```text
1. 后端电脑启动服务并保持运行
2. 电脑打开 http://127.0.0.1:8000/admin/mobile-connection
3. 手机连接与后台电脑相同的工厂 Wi-Fi
4. 小程序首次打开后点击“扫描电脑连接二维码”
5. 连接检查通过后，小程序自动保存地址并进入“计划”页
```

小程序底部只保留三个业务入口：

```text
计划
材料（钢板、余料、纸材）
成品
```

第一阶段已经完成连接配置和三个模块框架；现有成品、余料功能继续作为二级页面使用。计划匹配、钢板、纸材和成品分析按后续阶段逐项接入，小程序不显示图纸、操作日志、助手或 Excel 导出入口。

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
