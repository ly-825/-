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
│   │   └── inventory.py
│   └── services/
│       ├── dxf_parser.py
│       ├── qwen_service.py
│       └── qr_service.py
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
- 增加后台管理前端
- 增加小程序上传和扫码页面
- 增加 MySQL/PostgreSQL 生产配置
- 增加图纸模板规则库
