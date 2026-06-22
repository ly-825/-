# Windows 部署和更新操作手册

适用对象：

```text
在 Windows 电脑上运行杭州特耐时 DXF 智能用料系统的使用者。
```

系统组成：

```text
后端服务：FastAPI + SQLite
数据文件：data/app.db
上传图纸：data/uploads/
小程序代码：miniprogram/
```

重要原则：

```text
更新程序时只更新代码，不覆盖本机数据。
不要删除 data/、.env、.venv/、backups/。
```

## 一、第一次部署

### 1. 安装必备软件

请先安装：

```text
1. Git
2. Python 3
3. 微信开发者工具
```

安装 Python 时，建议勾选：

```text
Add python.exe to PATH
```

安装 Git 时，建议保留：

```text
Git Bash
```

后续更新时会更方便。

### 2. 下载项目

打开 Windows 的 `PowerShell` 或 `命令提示符 CMD`。

进入你想放项目的位置，例如桌面：

```bat
cd Desktop
```

下载项目：

```bat
git clone https://github.com/ly-825/-.git 杭州特耐时-backend
```

进入项目目录：

```bat
cd 杭州特耐时-backend
```

### 3. 创建 Python 虚拟环境

执行：

```bat
python -m venv .venv
```

如果提示找不到 `python`，可以试：

```bat
py -3 -m venv .venv
```

### 4. 安装依赖

执行：

```bat
.venv\Scripts\python -m pip install -r requirements.txt
```

如果下载很慢，可以稍后再处理；第一次安装需要联网。

### 5. 创建配置文件

执行：

```bat
copy .env.example .env
```

如果需要启用千问图纸识别，打开 `.env`，填写：

```text
DASHSCOPE_API_KEY=你的APIKey
```

如果没有 API Key，也可以运行系统，只是图纸识别会更依赖人工确认。

### 6. 配置高清图纸预览

如果需要让网页内显示更清楚的真实图纸预览，建议在运行后台服务的这台 Windows 电脑安装 QCAD Professional。

安装后，找到 QCAD 安装目录里的 `dwg2pdf.bat`。常见位置：

```text
C:\Program Files\QCAD\dwg2pdf.bat
C:\Program Files\QCAD Professional\dwg2pdf.bat
C:\Program Files\QCADCAM\dwg2pdf.bat
```

打开项目目录下的 `.env`，填写：

```text
DRAWING_PREVIEW_CONVERTER_PATH=C:\Program Files\QCAD\dwg2pdf.bat
DRAWING_PREVIEW_CONVERTER_ARGS=-auto-fit -paper=A4 -force -monochrome
DRAWING_PREVIEW_TIMEOUT_SECONDS=90
```

注意：

```text
只需要运行后台服务的电脑安装 QCAD。
其他访问后台的电脑或手机不需要安装 CAD。
如果图纸中文显示不完整，需要把图纸使用的 SHX 字体放到 QCAD 能识别的字体目录。
```

配置完成并重启后台后，进入后台的“图纸识别”页面，可以点击“批量生成高清预览”，为已经上传过的历史图纸生成 PDF 预览。

## 二、迁移旧电脑数据

如果这是全新试用，可以跳过本节。

如果要把旧电脑的业务数据带到 Windows 电脑，需要复制：

```text
data/app.db
data/uploads/
data/previews/
```

复制到 Windows 项目目录下：

```text
杭州特耐时-backend\data\app.db
杭州特耐时-backend\data\uploads\
杭州特耐时-backend\data\previews\
```

注意：

```text
app.db 是库存、图纸、流水等业务数据。
uploads 是上传过的 DXF 图纸文件。
previews 是已经生成的高清 PDF 预览文件，可以一起迁移；如果没有迁移，也可以后续重新生成。
app.db 和 uploads 必须一起迁移。
```

## 三、启动后端服务

进入项目目录：

```bat
cd 杭州特耐时-backend
```

启动服务：

```bat
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动成功后，窗口不要关闭。

在浏览器打开：

```text
http://127.0.0.1:8000/admin
```

健康检查地址：

```text
http://127.0.0.1:8000/health
```

如果看到：

```json
{"status":"ok"}
```

说明后端启动成功。

## 四、小程序配置

### 1. 查看 Windows 电脑 IP

打开 CMD，执行：

```bat
ipconfig
```

找到当前网络下面的：

```text
IPv4 地址
```

例如：

```text
192.168.31.99
```

### 2. 修改小程序接口地址

打开：

```text
miniprogram\app.js
```

把 `baseUrl` 改成 Windows 电脑 IP：

```js
baseUrl: 'http://192.168.31.99:8000'
```

注意：

```text
IP 要换成实际 Windows 电脑的 IPv4 地址。
端口保持 8000。
```

### 3. 微信开发者工具导入项目

打开微信开发者工具，选择：

```text
导入项目
```

项目目录选择：

```text
杭州特耐时-backend\miniprogram
```

真机预览时请确认：

```text
1. 手机和 Windows 电脑连接同一个 Wi-Fi
2. 后端服务已用 --host 0.0.0.0 启动
3. 微信开发者工具勾选“不校验合法域名、web-view、TLS 版本以及 HTTPS 证书”
```

## 五、日常启动流程

每天使用时：

```text
1. 打开项目文件夹
2. 双击 启动后台服务.bat
3. 打开后台或小程序
```

如果需要手动启动，也可以用命令：

```bat
cd 杭州特耐时-backend
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

后台地址：

```text
http://127.0.0.1:8000/admin
```

其他电脑或手机访问时，用 Windows 电脑 IP：

```text
http://192.168.31.99:8000/admin
```

## 六、更新程序

更新前请先停止后端服务。

如果后端窗口正在运行，按：

```text
Ctrl + C
```

停止服务。

### 方式一：双击一键更新，推荐

打开项目文件夹，双击：

```text
一键更新程序.bat
```

脚本会自动：

```text
1. 检查 Git 和 Python
2. 备份 data/app.db 和 data/uploads/
3. 检查本地代码是否有未保存改动
4. 拉取最新代码
5. 更新 Python 依赖
6. 询问是否立即启动后端服务
```

如果脚本最后询问是否启动后台服务，输入：

```text
Y
```

即可自动打开新的后台服务窗口。

### 方式二：使用 Git Bash 自动更新

右键项目目录，选择：

```text
Git Bash Here
```

执行：

```bash
bash scripts/update.sh
```

更新完成后，双击：

```text
启动后台服务.bat
```

### 方式三：手动更新

进入项目目录：

```bat
cd 杭州特耐时-backend
```

手动备份数据：

```bat
xcopy data backups\manual_backup /E /I /Y
```

拉取最新代码：

```bat
git pull
```

更新依赖：

```bat
.venv\Scripts\python -m pip install -r requirements.txt
```

重新启动后端：

```bat
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 七、更新后检查

更新后建议检查：

```text
1. 后台能打开
2. health 返回 ok
3. 库存数据还在
4. 图纸列表还在
5. 小程序能打开工作台
```

检查地址：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/admin
```

如果库存和图纸数据都还在，说明更新成功且数据没有被覆盖。

## 八、数据备份和恢复

### 1. 备份哪些文件

需要备份：

```text
data/app.db
data/uploads/
```

建议定期复制整个 `data` 文件夹。

### 2. 恢复数据

恢复前先停止后端服务。

然后把备份里的：

```text
app.db
uploads/
```

复制回：

```text
杭州特耐时-backend\data\
```

再重新启动后端服务。

## 九、常见问题

### 1. 提示 python 不是内部或外部命令

说明 Python 没加到 PATH。

可以尝试：

```bat
py -3 --version
```

如果可用，创建虚拟环境用：

```bat
py -3 -m venv .venv
```

### 2. 手机打不开小程序接口

检查：

```text
1. 手机和电脑是否同一个 Wi-Fi
2. 后端是否用 --host 0.0.0.0 启动
3. miniprogram/app.js 的 baseUrl 是否是电脑 IPv4 地址
4. Windows 防火墙是否拦截 8000 端口
```

必要时允许 Python 通过 Windows 防火墙。

### 3. 后台能打开，小程序打不开

通常是小程序 `baseUrl` 没改对。

确认：

```js
baseUrl: 'http://电脑IPv4地址:8000'
```

不要写成：

```js
baseUrl: 'http://127.0.0.1:8000'
```

真机里 `127.0.0.1` 代表手机自己，不是电脑。

### 4. 更新后数据不见了

先不要继续操作。

检查：

```text
data/app.db 是否还在
是否进入了正确的项目目录
是否有 backups/ 备份
```

如果有备份，把备份里的 `app.db` 和 `uploads` 恢复到 `data` 目录。

### 5. git pull 失败

如果提示本地有改动：

```text
Your local changes would be overwritten
```

说明这台电脑上有人改了代码文件。

普通使用者不要改代码。可以联系维护者处理，或先备份整份项目后再更新。

## 十、推荐使用方式

日常使用：

```text
每天启动后端服务
用后台或小程序操作库存
重要操作前备份 data
```

程序更新：

```text
维护者推送新版本
使用者双击 一键更新程序.bat
按提示启动后端服务
检查数据是否正常
```

不要手动删除：

```text
data/
.env
.venv/
backups/
```
