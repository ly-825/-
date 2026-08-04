# 小程序前端重设计验收

## 验收范围

- 视觉基准：`docs/superpowers/specs/assets/wechat-mini-program-materials-simple-direction.png`
- 实现截图：`artifacts/materials-home-implementation.png`
- 同屏对照：`artifacts/materials-home-comparison.png`
- 验收页面：`pages/materials/home`
- 验收状态：内网已连接，待确认余料 2 条

## 视口与归一化

- 开发者工具逻辑视口：390 × 671 CSS px，设备像素比 3。
- 实现截图：780 × 1342 px，截图密度为 2 图像像素 / CSS px。
- 参考稿：853 × 1844 px，完整概念画布约为 390 × 844 CSS px。
- 同屏对照将参考稿裁切为 853 × 1468 px，去除微信原生标签栏占位，再归一化到 780 × 1342 px。开发者工具自动化截图不包含微信原生导航栏和标签栏，这两部分作为运行时平台外壳排除在内容差异之外。

## 同屏对照结果

全页主要内容已在 `artifacts/materials-home-comparison.png` 中同屏检查：

- 页面层级一致：“材料”标题、连接状态、三个材料分类、唯一主操作。
- 颜色与语义一致：深蓝灰文字、绿色连接/主操作、橙色待处理状态、浅灰页面背景。
- 布局一致：白色分组容器、三行等高入口、左图标中文字右箭头、整宽主按钮。
- 视觉约束通过：无装饰性渐变、无卡片阴影、无英文 eyebrow，图标来自 Lucide 风格的本地 SVG 资产。
- 未发现裁切、溢出、边距错位、不一致圆角或低对比度文字。

本页所有特征性内容都完整出现在同一张全页对照图中，不存在需要放大才能判定的局部密集区域，因此本轮不再生成独立的局部对照图。

## 交互与构建验证

- 微信开发者工具 `preview` 构建成功，分包体积 128.0 KB。
- 自动化检查到 3 个 `.group-row` 与 1 个 `.primary-action`。
- 主操作目标路由 `pages/scraps/pending` 在开发者工具中成功打开。
- Python 完整测试：131 passed，33 subtests passed。
- Node 测试：7 passed。
- 小程序 JSON 配置解析通过，`git diff --check` 通过。

## 对照历史

1. 首次截图被内网 API 超时覆盖为错误态；调整自动化时序，在请求结束后注入固定成功态。
2. 第二次对照发现微信 `button` 默认宽度未填满容器；增加页面级 `min-width: 100%`。
3. 第三次对照发现分类行与图标密度高于参考稿；将页面级行高调整为 176rpx、图标调整为 88rpx，主按钮高度调整为 104rpx。
4. 最终同屏对照无 P0、P1 或 P2 可见问题。

final result: passed
