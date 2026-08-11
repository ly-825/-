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
envsubst '$TENAISHI_SITE_DOMAIN $TENAISHI_API_DOMAIN' \
  < /srv/tenaishi/app/deploy/nginx-personal-inventory.conf \
  > /etc/nginx/sites-available/tenaishi
nginx -t
```

模板仅代理到 `127.0.0.1:8000`，不包含额外 Basic Auth。应用自身的老板登录和员工小程序会话负责身份验证。
