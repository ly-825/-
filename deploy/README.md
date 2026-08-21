# 个人 ECS 原生部署与备份

本目录只使用通用路径，不记录公网 IP、域名、SSH 目标或真实密钥。个人 ECS 目标必须由项目所有者明确提供，并保存在仓库外。

固定目录：

```text
/srv/tenaishi/app       应用代码与虚拟环境
/srv/tenaishi/data      SQLite、上传文件、预览文件
/srv/tenaishi/backups   已校验的本地备份包
/etc/tenaishi           仅服务器使用的环境配置
```

## 每日自动备份

`tenaishi-backup.timer` 每天按 Asia/Shanghai 02:00 触发一次在线备份。数据库通过 SQLite backup API 生成一致快照，校验完整性并写入 SHA-256 清单后，备份目录才会正式生效。本地仅保留 7 天。

检查定时器和手工触发：

```bash
systemctl list-timers tenaishi-backup.timer
systemctl start tenaishi-backup.service
journalctl -u tenaishi-backup.service -n 100 --no-pager
find /srv/tenaishi/backups -maxdepth 2 -type f -print
```

不要直接读取并拷贝运行中的数据库文件。`scripts/backup.sh` 会使用文件锁，避免多个备份任务同时运行。

## 阿里云 ECS 文件备份

在阿里云控制台为这台个人 ECS 配置“文件备份”，至少包含：

```text
/srv/tenaishi/backups
/srv/tenaishi/data/uploads
```

云端保留策略设置为 30 天。启用后必须查看一次成功记录，确认备份字节数大于 0；只有显示成功且字节数非零，才算云端备份真正生效。本地 7 天备份不能代替云端 30 天副本。

## 恢复验证与演练

默认命令只校验，不写入任何目标：

```bash
/srv/tenaishi/app/.venv/bin/python scripts/restore_backup.py /srv/tenaishi/backups/年-月-日_时分秒
```

每月选择最新备份恢复到新的临时目录：

```bash
target=$(mktemp -d)/restored
/srv/tenaishi/app/.venv/bin/python scripts/restore_backup.py /srv/tenaishi/backups/年-月-日_时分秒 --target "$target"
sqlite3 "$target/app.db" 'PRAGMA integrity_check;'
```

输出必须为 `ok`。生产目标已存在时，工具默认拒绝覆盖；确需替换必须同时提供 `--replace --confirm RESTORE`，旧目标会先移动到同级 `pre-restore-年-月-日_时分秒` 目录，而不是删除。

## ICP 前限制

备案通过前只允许从所有者当前公网 IP 访问 SSH。安全组不得开放 80、443 或 8000，FastAPI 也只能监听 `127.0.0.1:8000`。个人 ECS 的实际安装和连接必须等待所有者明确提供并确认目标信息。

## 安全更新

服务器代码更新使用：

```bash
deploy/update-server.sh
```

脚本要求工作区干净，先完成一次已校验备份，再执行 fast-forward 更新、安装锁定依赖、重启单进程服务并等待本地健康检查。备份失败时不会切换 Git 版本。

## 备案通过后的 Nginx 模板

域名只从仓库外的个人部署目标文件读取。备案和证书准备完成后才渲染模板：

```bash
source "$HOME/.config/tenaishi/deploy-target.env"
envsubst '$TENAISHI_API_DOMAIN' \
  < /srv/tenaishi/app/deploy/nginx-personal-inventory.conf \
  > /etc/nginx/sites-available/tenaishi
nginx -t
```

模板仅代理到 `127.0.0.1:8000`，不包含额外 Basic Auth。应用自身的老板登录和员工小程序会话负责身份验证。

## 小程序正式版构建

开发版和体验版保留局域网扫码与手工连接。正式版不读取已保存的局域网地址，只使用仓库外部署目标文件中的个人 API 域名。

备案、HTTPS 证书和微信合法域名都就绪后，在项目根目录执行：

```bash
.venv/bin/python scripts/build_miniprogram_release.py \
  --target-file "$HOME/.config/tenaishi/deploy-target.env" \
  --output dist/miniprogram-release
```

构建器会拒绝缺失的域名、IP 地址和已存在的输出目录。在微信开发者工具中导入 `dist/miniprogram-release` 并上传。不要直接上传 `miniprogram/`，其 `release-config.js` 故意为空，用于在未正确构建时关闭正式连接。

## 微信管理员登录两阶段切换

生产切换必须分成阶段 A 和阶段 B。阶段 A 保留原密码/TOTP 应急入口，真实主管理员完成微信绑定和扫码登录之前，不得停用旧账号。所有命令都在 `/srv/tenaishi/app` 执行，激活码、会话令牌、OpenID 和哈希不得写入工单或聊天记录。

### 阶段 A：兼容部署和真实账号验证

1. 先运行 `scripts/backup.sh` 创建已校验备份，并记录备份目录。
2. 在 `/etc/tenaishi/tenaishi.env` 设置 `LEGACY_PASSWORD_LOGIN_ENABLED=true`，部署新代码并重启服务。
3. 对备份副本或恢复到临时目录的数据库执行：

   ```bash
   sqlite3 临时恢复目录/app.db 'PRAGMA integrity_check;'
   sqlite3 临时恢复目录/app.db 'PRAGMA foreign_key_check;'
   ```

   第一条必须输出 `ok`，第二条必须无输出。重复运行应用迁移，不能出现重复表或重复索引错误。
4. 验证 `/health`、旧老板应急登录、员工小程序登录和 `/auth/login` 扫码页。
5. 以 `tenaishi` 服务账号在 ECS 终端创建唯一主管理员：

   ```bash
   .venv/bin/python scripts/manage_superadmin.py bootstrap --username admin --display-name 主管理员
   ```

   30 分钟一次性激活码只交给真实主管理员本人。
6. 主管理员在小程序绑定微信，完成一次“小程序扫码—手机确认—电脑进入后台”的完整登录。
7. 主管理员在“账号管理”新增一个真实或测试老板；该老板使用不同微信绑定并完成扫码登录。

### 阶段 B：关闭临时凭据

只有阶段 A 的两个真实管理员都验证通过后，才执行：

1. 再运行一次 `scripts/backup.sh`，保存切换前第二份已校验备份。
2. 停用临时 `owner` 账号并递增其 `session_version`，使现有 PC 和小程序会话立即失效；不要删除业务数据、流水、图纸或上传文件。
3. 从生产配置中移除或轮换临时账号使用的 TOTP 密钥。
4. 设置 `LEGACY_PASSWORD_LOGIN_ENABLED=false` 并重启服务。
5. 验证 `/auth/legacy-login` 的 GET 和 POST 均返回 404。
6. 重新验证主管理员扫码登录、老板扫码登录、老板管理员工、员工库存访问、HTTPS 健康，以及公网端口 8000 仍关闭。老板管理老板/主管理员、员工确认二维码都必须返回 403。

### 主管理员更换微信与回滚

主管理员遗失或更换微信时，先运行 `scripts/backup.sh`，再在 ECS 终端执行：

```bash
.venv/bin/python scripts/manage_superadmin.py reset-wechat --username <准确的主管理员账号>
```

新激活码只有 30 分钟有效，只交给真实主管理员本人。重置会递增 `session_version`，旧微信和旧会话立即失效。

若新版本发生阻断性故障，可以临时恢复上一已验证代码并设置 `LEGACY_PASSWORD_LOGIN_ENABLED=true`。不要自动恢复已停用的临时 `owner` 凭据；只有经完整性校验的备份和明确的事故决策同时要求时，才考虑恢复。回滚后必须再次执行健康、权限、扫码页和公网 8000 关闭检查。
