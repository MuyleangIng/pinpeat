"""Draws the Pinpeat logo (avatar 1024x1024) and banner (1360x480) as PNG.
Run: uv run --python 3.12 --with playwright python assets/make_logo.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(__file__).parent

# gold play button inside sound rings, on a deep night-blue → plum circle; reads well when Discord crops it round
MARK = """
<svg viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="bg" cx="35%" cy="28%" r="85%">
      <stop offset="0" stop-color="#3b2a7a"/><stop offset=".55" stop-color="#1d1846"/><stop offset="1" stop-color="#0d0b22"/>
    </radialGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffe7a3"/><stop offset=".5" stop-color="#f4b740"/><stop offset="1" stop-color="#d9822b"/>
    </linearGradient>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="18" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="1024" height="1024" fill="url(#bg)"/>
  <g fill="none" stroke="url(#gold)" stroke-linecap="round">
    <circle cx="512" cy="512" r="300" stroke-width="22" opacity=".95"/>
    <path d="M512 132 A380 380 0 0 1 892 512" stroke-width="16" opacity=".55"/>
    <path d="M512 892 A380 380 0 0 1 132 512" stroke-width="16" opacity=".55"/>
    <path d="M512 72 A440 440 0 0 1 952 512" stroke-width="10" opacity=".25"/>
    <path d="M512 952 A440 440 0 0 1 72 512" stroke-width="10" opacity=".25"/>
  </g>
  <!-- play triangle, rounded, slightly right of centre so it looks centred -->
  <path filter="url(#glow)" fill="url(#gold)"
        d="M440 368 Q440 336 468 352 L676 482 Q704 500 676 518 L468 648 Q440 664 440 632 Z"/>
  <!-- small kbach-style flame tip on top of the ring -->
  <path fill="url(#gold)" d="M512 168 C540 196 548 224 512 252 C476 224 484 196 512 168 Z"/>
  <circle cx="512" cy="276" r="10" fill="url(#gold)"/>
</svg>
"""

AVATAR = f"<html><body style='margin:0'>{MARK}</body></html>"

BANNER = f"""<html><head>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@700;800&family=Noto+Sans+Khmer:wght@600&display=swap" rel="stylesheet">
<style>
 body{{margin:0;width:1360px;height:480px;overflow:hidden;
   background:radial-gradient(120% 140% at 20% 20%,#3b2a7a 0,#1d1846 55%,#0d0b22 100%);
   display:flex;align-items:center;gap:56px;padding:0 90px;box-sizing:border-box;font-family:Inter,sans-serif}}
 .m{{width:300px;height:300px;border-radius:50%;overflow:hidden;flex:none;box-shadow:0 0 0 6px #f4b74055,0 20px 60px #0008}}
 .m svg{{width:100%;height:100%}}
 h1{{margin:0;font-size:132px;font-weight:800;letter-spacing:-3px;
   background:linear-gradient(135deg,#ffe7a3,#f4b740 50%,#d9822b);-webkit-background-clip:text;color:transparent;line-height:1}}
 .km{{font-family:'Noto Sans Khmer';font-size:58px;color:#f4d9a0;margin-top:14px}}
 .sub{{font-size:30px;color:#b9b3e6;margin-top:16px;font-weight:700}}
</style></head><body>
 <div class="m">{MARK}</div>
 <div><h1>Pinpeat</h1><div class="km">ពិណពាទ្យ</div><div class="sub">YouTube music for your server</div></div>
</body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1024, "height": 1024})
    pg.set_content(AVATAR)
    pg.screenshot(path=OUT / "pinpeat-avatar.png")
    pg = b.new_page(viewport={"width": 1360, "height": 480})
    pg.set_content(BANNER, wait_until="networkidle")
    pg.evaluate("document.fonts.ready")
    pg.screenshot(path=OUT / "pinpeat-banner.png")
    b.close()
print("saved", OUT / "pinpeat-avatar.png", OUT / "pinpeat-banner.png")
