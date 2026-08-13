# Windows 一键更新脚本 Pack 锁定修复设计

## 目标

修复 Windows 正式电脑执行 `一键更新程序.bat` 时，`git pull` 在 fetch 后自动维护仓库并删除旧 pack 文件失败，出现 `Unlink of file ... failed` 的问题。

更新器必须继续保护本地程序文件，不覆盖未提交改动，并且正式电脑只能更新远程 `origin/main`。

## 方案

将原来的：

```bat
git pull --ff-only
```

拆分为两个明确步骤：

```bat
git fetch --no-auto-maintenance origin main
git merge --ff-only FETCH_HEAD
```

`fetch` 禁止本次命令结束后运行自动维护，避免触发旧 pack 文件重组与删除。`merge --ff-only` 保留原脚本只允许快进更新、不生成合并提交的安全边界。

脚本在备份和工作区检查之前确认当前分支为 `main`。处于开发分支或 detached HEAD 时停止，并提示联系管理员，避免正式电脑误合并开发线路。

## 错误处理

- 分支不是 `main`：停止更新，不执行 fetch 或 merge。
- 工作区存在已跟踪或未跟踪的程序文件改动：沿用当前保护逻辑并停止更新。
- fetch 失败：显示“下载代码失败”，保留现有代码和数据备份。
- merge 不能快进：显示“无法安全更新到正式版本”，不自动 reset、stash 或覆盖文件。
- 不使用 `echo y` 自动回答重试提示，因为文件持续被锁定时立即重试仍会失败。

## 兼容与上线

目标电脑使用支持 `--no-auto-maintenance` 的 Git 2.29 或更高版本。暂不强制升级到修复 Windows pack 锁定问题的新版本；更新脚本通过跳过 fetch 后自动维护规避问题。

修复提交必须从干净的 `origin/main` 创建，避免混入当前开发目录中的账号权限、生产部署或未提交修改。提交合并并推送到远程 `main` 后，受影响电脑首次通过 CMD 手工执行：

```bat
git fetch --no-auto-maintenance origin main
git merge --ff-only FETCH_HEAD
```

获取新版脚本；后续继续双击脚本更新。

## 验证

- 静态检查批处理分支、错误码和括号结构。
- 在隔离的临时 Git 仓库中模拟：`main` 正常快进、非 `main` 拒绝、脏工作区拒绝、无法快进拒绝。
- 确认提交只包含设计文档、更新脚本及对应测试资产。
- 不在本地 macOS 环境声称复现 Windows 文件锁；最终仍需在受影响 Windows 电脑执行一次真实更新验收。

## 非目标

- 不修改业务代码、数据库或备份内容。
- 不自动清理 pack 文件，不全局关闭 Git maintenance。
- 不自动 reset、stash、强制 checkout 或删除本地改动。
- 不在本次修改中升级目标电脑的 Git。
