from fastapi.responses import HTMLResponse


def render_home() -> HTMLResponse:
    html = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>杭州特耐时｜DXF智能用料系统</title>
  <style>
    :root {
      --bg: #f4f7fb;
      --card: #ffffff;
      --text: #172033;
      --muted: #667085;
      --primary: #1d4ed8;
      --primary-dark: #173ea6;
      --line: #e6eaf2;
      --good: #16a34a;
      --warn: #f59e0b;
      --shadow: 0 20px 60px rgba(20, 32, 55, .10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 12% 0%, rgba(29,78,216,.18), transparent 32%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      min-height: 100vh;
    }
    .shell { max-width: 1180px; margin: 0 auto; padding: 34px 22px 56px; }
    .hero {
      background: linear-gradient(135deg, #102554 0%, #1d4ed8 56%, #38bdf8 100%);
      border-radius: 28px;
      color: white;
      padding: 38px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }
    .hero:after {
      content: "";
      position: absolute;
      width: 360px;
      height: 360px;
      right: -90px;
      top: -130px;
      border-radius: 50%;
      background: rgba(255,255,255,.14);
    }
    .eyebrow { opacity: .84; font-size: 14px; letter-spacing: .18em; text-transform: uppercase; }
    h1 { margin: 12px 0 12px; font-size: 38px; line-height: 1.15; }
    .hero p { max-width: 760px; margin: 0; color: rgba(255,255,255,.86); font-size: 17px; line-height: 1.8; }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 26px; }
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 44px;
      padding: 0 18px;
      border-radius: 999px;
      text-decoration: none;
      font-weight: 700;
      transition: .2s ease;
    }
    .btn.primary { background: white; color: var(--primary); }
    .btn.ghost { color: white; border: 1px solid rgba(255,255,255,.35); }
    .btn:hover { transform: translateY(-1px); }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-top: 22px; }
    .card {
      background: rgba(255,255,255,.88);
      backdrop-filter: blur(14px);
      border: 1px solid rgba(230,234,242,.9);
      border-radius: 22px;
      padding: 22px;
      box-shadow: 0 12px 34px rgba(20, 32, 55, .06);
    }
    .card h3 { margin: 0 0 8px; font-size: 18px; }
    .card p { margin: 0; color: var(--muted); line-height: 1.65; font-size: 14px; }
    .icon {
      width: 42px;
      height: 42px;
      border-radius: 14px;
      display: grid;
      place-items: center;
      margin-bottom: 16px;
      background: #eef4ff;
      color: var(--primary);
      font-weight: 900;
    }
    .panel { display: grid; grid-template-columns: 1.2fr .8fr; gap: 18px; margin-top: 18px; }
    .flow { display: grid; gap: 10px; margin-top: 12px; }
    .step { display: flex; align-items: center; gap: 12px; padding: 13px 14px; border: 1px solid var(--line); border-radius: 16px; background: #fbfdff; }
    .num { width: 28px; height: 28px; border-radius: 50%; background: var(--primary); color: white; display: grid; place-items: center; font-size: 13px; font-weight: 800; flex: none; }
    .status { display: grid; gap: 12px; margin-top: 12px; }
    .status-item { display: flex; justify-content: space-between; padding: 13px 0; border-bottom: 1px solid var(--line); color: var(--muted); }
    .status-item strong { color: var(--text); }
    code { background: #eef2ff; color: #1e40af; padding: 3px 7px; border-radius: 8px; }
    @media (max-width: 900px) { .grid { grid-template-columns: repeat(2, 1fr); } .panel { grid-template-columns: 1fr; } h1 { font-size: 30px; } }
    @media (max-width: 560px) { .grid { grid-template-columns: 1fr; } .hero { padding: 26px; border-radius: 22px; } }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">DXF Material Matching System</div>
      <h1>DXF智能识别用料与余料匹配系统</h1>
      <p>上传产品图纸后，系统自动提取产品编号、材料、厚度、最大外径和可回收余料尺寸，帮助车间完成产品库存和余料流转管理。</p>
      <div class="actions">
        <a class="btn primary" href="/admin">进入系统后台</a>
        <a class="btn ghost" href="/admin/drawings">上传图纸</a>
        <a class="btn ghost" href="/admin/inventory">库存查询</a>
      </div>
    </section>

    <section class="grid">
      <div class="card"><div class="icon">图</div><h3>图纸上传</h3><p>支持 DXF 文件上传，自动解析文本、尺寸标注、圆和外接矩形。</p></div>
      <div class="card"><div class="icon">智</div><h3>智能识别</h3><p>结合 ezdxf 与通义千问，识别产品编号、名称、材质、厚度和余料建议。</p></div>
      <div class="card"><div class="icon">库</div><h3>库存管理</h3><p>产品入库、产品出库和库存流水集中管理，便于仓库核对数量。</p></div>
      <div class="card"><div class="icon">余</div><h3>余料记录</h3><p>切割后的中心余料自动登记，便于后续库存复用和来源追溯。</p></div>
    </section>

    <section class="panel">
      <div class="card">
        <h3>第一版业务流程</h3>
        <div class="flow">
          <div class="step"><span class="num">1</span><span>上传 DXF 图纸，后端自动解析候选数据</span></div>
          <div class="step"><span class="num">2</span><span>人工确认产品编号、材质、厚度和尺寸</span></div>
          <div class="step"><span class="num">3</span><span>根据确认图纸进行产品入库</span></div>
          <div class="step"><span class="num">4</span><span>产品入库后自动生成待入库余料</span></div>
          <div class="step"><span class="num">5</span><span>仓库确认余料尺寸和库位，并按需要进行余料出库</span></div>
        </div>
      </div>
      <div class="card">
        <h3>常用入口</h3>
        <div class="status">
          <div class="status-item"><span>系统后台</span><strong><code>/admin</code></strong></div>
          <div class="status-item"><span>图纸上传</span><strong><code>/admin/drawings</code></strong></div>
          <div class="status-item"><span>产品入库</span><strong><code>/admin/inventory/inbound</code></strong></div>
          <div class="status-item"><span>余料管理</span><strong><code>/admin/scraps</code></strong></div>
        </div>
      </div>
    </section>
  </main>
</body>
</html>
    """
    return HTMLResponse(html)
