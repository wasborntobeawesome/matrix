"""Generate the example assets for the studio: screenshots of a FICTIONAL app ("Nimbo").

    video/.venv/Scripts/python video/studio/example/make_example.py

Writes next to this file:
    home.png, goals.png, insights.png   mobile screenshots (1170x2532, light theme)
    web-dashboard.png                    web screenshot (1440x900)
    add-expense.mp4                      4 s H.264 screen recording (780x1688)
All names, numbers and people are made up. brief.json is hand-written.
"""
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FFMPEG = HERE.parent.parent / "bin" / "ffmpeg.exe"

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", system-ui, sans-serif; background: #F4F6FB; color: #121826; }
.app { width: 390px; height: 844px; overflow: hidden; position: relative; background: #F4F6FB; }
.status { height: 50px; display: flex; justify-content: space-between; align-items: center; padding: 6px 28px 0;
          font-weight: 600; font-size: 15px; }
.top { padding: 8px 22px 6px; display: flex; justify-content: space-between; align-items: center; }
.hello small { color: #6B7280; font-size: 13px; } .hello div { font-size: 22px; font-weight: 700; }
.avatar { width: 42px; height: 42px; border-radius: 50%; background: linear-gradient(135deg,#FFB547,#FF7A59);
          display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; }
.card { margin: 14px 22px; border-radius: 24px; padding: 20px; color: #fff;
        background: linear-gradient(135deg, #3D7BFF, #6A5CFF); box-shadow: 0 14px 30px rgba(61,123,255,.35); }
.card small { opacity: .8; font-size: 13px; } .card .big { font-size: 36px; font-weight: 800; margin: 6px 0 14px; }
.card .row { display: flex; gap: 10px; } .card .row div { flex: 1; background: rgba(255,255,255,.16);
             border-radius: 14px; padding: 9px 11px; font-size: 12px; } .card .row b { display: block; font-size: 15px; }
.acts { display: flex; justify-content: space-between; margin: 6px 22px 10px; }
.act { width: 78px; text-align: center; font-size: 12px; color: #374151; }
.act i { display: flex; width: 54px; height: 54px; margin: 0 auto 6px; border-radius: 18px; background: #fff;
         align-items: center; justify-content: center; font-style: normal; font-size: 22px; box-shadow: 0 4px 12px rgba(0,0,0,.06); }
.panel { margin: 10px 22px; background: #fff; border-radius: 22px; padding: 16px; box-shadow: 0 6px 18px rgba(18,24,38,.05); }
.panel h3 { font-size: 15px; display: flex; justify-content: space-between; } .panel h3 span { color: #3D7BFF; font-weight: 600; font-size: 13px; }
.bars { display: flex; align-items: flex-end; gap: 10px; height: 92px; margin-top: 12px; }
.bars div { flex: 1; border-radius: 8px 8px 4px 4px; background: #DCE6FF; } .bars div.on { background: #3D7BFF; }
.days { display: flex; gap: 10px; font-size: 11px; color: #9CA3AF; margin-top: 6px; } .days span { flex: 1; text-align: center; }
.tx { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-top: 1px solid #F1F3F8; }
.tx:first-of-type { border-top: 0; }
.tx i { width: 40px; height: 40px; border-radius: 13px; display: flex; align-items: center; justify-content: center; font-style: normal; font-size: 19px; }
.tx div { flex: 1; font-size: 14px; font-weight: 600; } .tx small { display: block; color: #9CA3AF; font-weight: 400; font-size: 12px; }
.tx b { font-size: 14px; }
.tab { position: absolute; left: 0; right: 0; bottom: 0; height: 84px; background: #fff; display: flex;
       justify-content: space-around; padding-top: 12px; border-top: 1px solid #EEF0F5; font-size: 11px; color: #9CA3AF; }
.tab div { text-align: center; } .tab div i { display: block; font-style: normal; font-size: 21px; } .tab .on { color: #3D7BFF; font-weight: 600; }
.title { padding: 6px 22px 4px; font-size: 28px; font-weight: 800; }
.goal { display: flex; gap: 14px; align-items: center; }
.ringg { width: 64px; height: 64px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; }
.goal .meta { flex: 1; } .goal .meta b { font-size: 16px; } .goal .meta small { display: block; color: #6B7280; font-size: 12px; margin-top: 2px; }
.prog { height: 8px; background: #EEF1F7; border-radius: 4px; margin-top: 8px; overflow: hidden; } .prog i { display: block; height: 100%; border-radius: 4px; }
.pie { width: 170px; height: 170px; border-radius: 50%; margin: 6px auto 12px;
       background: conic-gradient(#3D7BFF 0 38%, #34D399 38% 62%, #FFB547 62% 80%, #FF7A59 80% 92%, #A78BFA 92% 100%);
       display: flex; align-items: center; justify-content: center; }
.pie div { width: 108px; height: 108px; border-radius: 50%; background: #fff; display: flex; flex-direction: column;
           align-items: center; justify-content: center; font-weight: 800; font-size: 20px; } .pie small { font-size: 11px; color: #6B7280; font-weight: 500; }
.leg { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; font-size: 13px; }
.leg span { display: flex; align-items: center; gap: 8px; } .leg i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.tip { margin: 10px 22px; border-radius: 20px; padding: 14px 16px; background: #E8FBF3; color: #0F5132; font-size: 14px; display: flex; gap: 10px; }
/* add expense */
.amount { text-align: center; margin: 26px 0 8px; font-size: 56px; font-weight: 800; letter-spacing: -1px; }
.amount small { font-size: 30px; color: #9CA3AF; vertical-align: top; }
.cats { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin: 14px 22px; }
.cat { padding: 10px 14px; border-radius: 14px; background: #fff; font-size: 14px; font-weight: 600; border: 2px solid transparent; }
.cat.on { border-color: #3D7BFF; background: #EEF3FF; color: #2952CC; }
.keys { position: absolute; left: 22px; right: 22px; bottom: 120px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.key { height: 58px; border-radius: 16px; background: #fff; display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: 600; }
.key.hit { background: #DCE6FF; }
.save { position: absolute; left: 22px; right: 22px; bottom: 40px; height: 60px; border-radius: 18px; background: #3D7BFF; color: #fff;
        display: flex; align-items: center; justify-content: center; font-size: 18px; font-weight: 700; }
.toast { position: absolute; left: 50%; top: 110px; transform: translateX(-50%); background: #121826; color: #fff; border-radius: 16px;
         padding: 12px 18px; font-size: 15px; font-weight: 600; white-space: nowrap; }
"""

STATUS = '<div class="status"><span>9:41</span><span>●●● ▮</span></div>'
TAB = lambda on: ('<div class="tab">' + "".join(
    f'<div class="{"on" if k == on else ""}"><i>{i}</i>{k}</div>'
    for k, i in [("Home", "⌂"), ("Goals", "◎"), ("Insights", "◔"), ("Profile", "☺")]) + "</div>")


def home():
    tx = [("☕", "#FFF4E0", "Bean & Leaf Café", "Today · Coffee", "-$4.80"),
          ("🛒", "#E8FBF3", "Fresh Market", "Yesterday · Groceries", "-$62.15"),
          ("🚌", "#EEF3FF", "City Transit", "Mon · Transport", "-$2.50"),
          ("💼", "#F3EEFF", "Salary", "Mon · Income", "+$2,400.00")]
    bars = [38, 64, 45, 82, 56, 30, 70]
    return f"""<div class="app">{STATUS}
<div class="top"><div class="hello"><small>Good morning</small><div>Hi, Sam 👋</div></div><div class="avatar">S</div></div>
<div class="card"><small>Total balance</small><div class="big">$4,280.50</div>
<div class="row"><div>Checking<b>$2,915.20</b></div><div>Savings<b>$1,365.30</b></div></div></div>
<div class="acts">{"".join(f'<div class="act"><i>{i}</i>{t}</div>' for i, t in [("＋", "Add"), ("⇄", "Transfer"), ("◎", "Goals"), ("☰", "Budget")])}</div>
<div class="panel"><h3>Spending this week <span>$412</span></h3>
<div class="bars">{"".join(f'<div class="{"on" if i == 3 else ""}" style="height:{b}%"></div>' for i, b in enumerate(bars))}</div>
<div class="days">{"".join(f"<span>{d}</span>" for d in "MTWTFSS")}</div></div>
<div class="panel"><h3>Recent <span>See all</span></h3>
{"".join(f'<div class="tx"><i style="background:{bg}">{e}</i><div>{n}<small>{s}</small></div><b style="color:{"#059669" if a[0] == "+" else "#121826"}">{a}</b></div>' for e, bg, n, s, a in tx)}
</div>{TAB("Home")}</div>"""


def goals():
    gs = [("🏝️", "Summer trip", "$1,360 of $2,000", 68, "#3D7BFF"), ("💻", "New laptop", "$480 of $1,200", 40, "#A78BFA"),
          ("🛟", "Safety fund", "$4,250 of $5,000", 85, "#34D399"), ("🎁", "Gifts", "$90 of $300", 30, "#FFB547")]
    items = "".join(
        f'<div class="panel goal"><div class="ringg" style="background:conic-gradient({c} 0 {p}%, #EEF1F7 {p}% 100%)">'
        f'<div style="width:50px;height:50px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-size:20px">{e}</div></div>'
        f'<div class="meta"><b>{n}</b><small>{s} · {p}%</small><div class="prog"><i style="width:{p}%;background:{c}"></i></div></div></div>'
        for e, n, s, p, c in gs)
    return f"""<div class="app">{STATUS}<div class="title">Goals</div>
<div style="padding:0 22px;color:#6B7280;font-size:14px">You're saving <b style="color:#059669">$310/month</b> on average</div>
{items}<div class="tip">✨ <span>Round up purchases to reach <b>Summer trip</b> 3 weeks sooner.</span></div>{TAB("Goals")}</div>"""


def insights():
    leg = [("#3D7BFF", "Home 38%"), ("#34D399", "Food 24%"), ("#FFB547", "Transport 18%"), ("#FF7A59", "Fun 12%"), ("#A78BFA", "Other 8%")]
    return f"""<div class="app">{STATUS}<div class="title">Insights</div>
<div class="panel"><h3>October <span>vs September ↓ 12%</span></h3><div class="pie"><div>$1,842<small>spent</small></div></div>
<div class="leg">{"".join(f'<span><i style="background:{c}"></i>{t}</span>' for c, t in leg)}</div></div>
<div class="panel"><h3>Top changes</h3>
<div class="tx"><i style="background:#E8FBF3">🍽️</i><div>Eating out<small>8 fewer orders</small></div><b style="color:#059669">-$96</b></div>
<div class="tx"><i style="background:#FFF4E0">⚡</i><div>Utilities<small>Cold week</small></div><b style="color:#DC2626">+$21</b></div></div>
{TAB("Insights")}</div>"""


def add_expense(step: int):
    """Frames of the 'add expense' recording. step 0..119 (30 fps, 4 s)."""
    typed = "12.50"
    n = min(len(typed), max(0, (step - 8) // 9))
    amount = typed[:n] or "0"
    cat_on = step >= 62
    pressed = typed[n - 1] if 0 < n and (step - 8) % 9 < 4 and step < 60 else None
    save_hit = 84 <= step < 92
    toast = step >= 92
    keys = "".join(f'<div class="key {"hit" if pressed == k else ""}">{k}</div>' for k in "123456789.0⌫")
    cats = "".join(f'<div class="cat {"on" if cat_on and c.endswith("Coffee") else ""}">{c}</div>'
                   for c in ["🛒 Groceries", "☕ Coffee", "🚌 Transport", "🍽️ Eating out", "🎬 Fun"])
    return f"""<div class="app">{STATUS}<div class="title">Add expense</div>
<div class="amount"><small>$</small>{amount}</div>
<div style="text-align:center;color:#6B7280;font-size:14px">Bean &amp; Leaf Café · Today</div>
<div class="cats">{cats}</div>
<div class="keys">{keys}</div>
<div class="save" style="{"background:#2952CC;transform:scale(.97)" if save_hit else ""}">Save expense</div>
{'<div class="toast">✓ Saved to Coffee · $12.50</div>' if toast else ""}
</div>"""


def web():
    rows = [("Bean & Leaf Café", "Coffee", "Oct 14", "-$4.80"), ("Fresh Market", "Groceries", "Oct 13", "-$62.15"),
            ("City Transit", "Transport", "Oct 12", "-$2.50"), ("Northwind Rent Co.", "Home", "Oct 01", "-$1,150.00")]
    cols = [52, 61, 47, 70, 58, 66, 49, 74, 63, 55, 68, 80]
    return f"""<div style="width:1440px;height:900px;display:flex;background:#F4F6FB;font-family:'Segoe UI',sans-serif">
<div style="width:240px;background:#0F1730;color:#C7D2FE;padding:28px 20px;font-size:15px">
<div style="color:#fff;font-weight:800;font-size:22px;margin-bottom:34px">◐ Nimbo</div>
{"".join(f'<div style="padding:11px 14px;border-radius:12px;margin-bottom:6px;{"background:#3D7BFF;color:#fff" if i == 0 else ""}">{t}</div>' for i, t in enumerate(["Dashboard", "Budgets", "Goals", "Accounts", "Reports", "Settings"]))}
</div>
<div style="flex:1;padding:30px 36px">
<div style="display:flex;justify-content:space-between;align-items:center"><div style="font-size:28px;font-weight:800">October plan</div>
<div style="background:#3D7BFF;color:#fff;padding:11px 18px;border-radius:12px;font-weight:600">+ New budget</div></div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:24px">
{"".join(f'<div style="background:#fff;border-radius:18px;padding:20px"><div style="color:#6B7280;font-size:14px">{a}</div><div style="font-size:30px;font-weight:800;margin-top:6px">{b}</div><div style="color:{c};font-size:13px;margin-top:4px">{d}</div></div>' for a, b, c, d in [("Income", "$4,800", "#059669", "↑ 4% vs Sep"), ("Planned spend", "$3,200", "#6B7280", "11 budgets"), ("Left to spend", "$1,358", "#3D7BFF", "on track ✓")])}
</div>
<div style="display:grid;grid-template-columns:1.5fr 1fr;gap:20px;margin-top:20px">
<div style="background:#fff;border-radius:18px;padding:22px"><div style="font-weight:700;font-size:17px">Cash flow · 12 months</div>
<div style="display:flex;align-items:flex-end;gap:14px;height:230px;margin-top:18px">
{"".join(f'<div style="flex:1;height:{v}%;border-radius:8px 8px 3px 3px;background:{"#3D7BFF" if i == 11 else "#C9D7FF"}"></div>' for i, v in enumerate(cols))}</div>
<div style="display:flex;gap:14px;color:#9CA3AF;font-size:12px;margin-top:8px">{"".join(f'<span style="flex:1;text-align:center">{m}</span>' for m in "NDJFMAMJJASO")}</div></div>
<div style="background:#fff;border-radius:18px;padding:22px"><div style="font-weight:700;font-size:17px">Budgets</div>
{"".join(f'<div style="margin-top:16px;font-size:14px"><div style="display:flex;justify-content:space-between"><span>{n}</span><b>{p}%</b></div><div style="height:9px;background:#EEF1F7;border-radius:5px;margin-top:6px"><div style="width:{p}%;height:100%;border-radius:5px;background:{c}"></div></div></div>' for n, p, c in [("Groceries", 72, "#34D399"), ("Eating out", 45, "#FFB547"), ("Transport", 30, "#3D7BFF"), ("Fun", 88, "#FF7A59")])}
</div></div>
<div style="background:#fff;border-radius:18px;padding:18px 22px;margin-top:20px;font-size:14px">
{"".join(f'<div style="display:flex;padding:9px 0;border-top:{"0" if i == 0 else "1px solid #F1F3F8"}"><span style="flex:2;font-weight:600">{a}</span><span style="flex:1;color:#6B7280">{b}</span><span style="flex:1;color:#6B7280">{c}</span><b style="flex:1;text-align:right">{d}</b></div>' for i, (a, b, c, d) in enumerate(rows))}
</div></div></div>"""


def shot(page, html, path, w, h, scale):
    page.set_viewport_size({"width": w, "height": h})
    page.set_content(f"<style>{CSS}</style>{html}")
    page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": w, "height": h})


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        m3 = b.new_page(device_scale_factor=3)
        for name, fn in [("home", home), ("goals", goals), ("insights", insights)]:
            shot(m3, fn(), HERE / f"{name}.png", 390, 844, 3)
            print(HERE / f"{name}.png")
        w1 = b.new_page(device_scale_factor=1)
        shot(w1, web(), HERE / "web-dashboard.png", 1440, 900, 1)
        print(HERE / "web-dashboard.png")
        m2 = b.new_page(device_scale_factor=2)
        m2.set_viewport_size({"width": 390, "height": 844})
        ff = subprocess.Popen([str(FFMPEG), "-y", "-v", "error", "-f", "image2pipe", "-framerate", "30", "-c:v", "png",
                               "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                               "-movflags", "+faststart", str(HERE / "add-expense.mp4")], stdin=subprocess.PIPE)
        for step in range(120):
            m2.set_content(f"<style>{CSS}</style>{add_expense(step)}")
            ff.stdin.write(m2.screenshot(type="png"))
        ff.stdin.close()
        ff.wait()
        print(HERE / "add-expense.mp4")
        b.close()


if __name__ == "__main__":
    main()
