"""Generate realistic small-content screenshots at TRUE physical font sizes.

Prior datasets (gen_normalize_dataset.py, gen_small_dataset.py) rendered at
device_scale_factor=1 with font sizes 10-24px, yielding 12-16px glyphs -
systemically ~1/3 SMALLER than text in real screenshots on a typical
150%-scaling display (2560x1600 etc), where a 9pt Windows UI label is 18px
tall and a 12pt terminal prompt is 24px.  Those datasets made "small image"
look smaller/harder than reality, biasing limit_side_len conclusions.

This dataset fixes that by:
  1. device_scale_factor=1.5 -> Chromium renders at 150% physical pixels,
     so a 9pt/12px UI label comes out at the real ~18px glyph height.
  2. Font sizes = nominal real-world values (9pt UI, 12pt terminal, 14px
     code, 16px web), not an arbitrary 10-24px spread.
  3. Content = short strings (single words / labels / values / single
     lines) so the cropped image is SMALL AREA while glyphs stay crisp and
     readable - the real HushSnap "small screenshot" scenario.

Result: glyph heights ~18-24px, image short sides ~20-45px.  This is the
valid probe for Det.limit_side_len on small-but-readable screenshots.
"""
from __future__ import annotations

import argparse
import json
import random
from html import escape
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent

# ── Short, realistic screenshot content ─────────────────────────────────────
# A user OCRs these as small crops.  Kept short so the cropped image is small
# while the glyphs stay at a normal, readable size.
_CJK_LABELS = ["测试", "保存", "取消", "确认", "设置", "同步", "删除", "编辑",
               "复制", "粘贴", "撤销", "重做", "刷新", "搜索", "导出", "导入",
               "登录", "退出", "启动", "下载", "打开", "关闭", "应用", "重置",
               "最小化", "最大化", "恢复", "暂停", "继续", "完成"]
_LATIN_LABELS = ["OK", "Cancel", "Save", "Delete", "Edit", "Copy", "Paste", "Cut",
                "Undo", "Redo", "Refresh", "Search", "Export", "Login", "Logout",
                "Start", "Stop", "Pause", "Resume", "Download", "Open", "Close",
                "Apply", "Reset", "Yes", "No", "Retry", "Ignore"]
_VALUES = ["100%", "8080", "15:30", "2026-07-24", "v3.9.2", "3.14", "1024",
           "C:\\Users", "+86 138", "0.95", "56,789", "12ms", "TTL=56", "404",
           "200", "utf-8", "#0066cc", "3.9.2"]
_PROMPTS = ["PS C:\\>", "PS C:\\project>", "PS C:\\Users\\me>", "user@host:~$",
            "$", ">>>", "mysql>", "#", "C:\\>", "admin@db:~$"]
_TOKENS = ["def", "return", "import", "async", "await", "True", "False", "None",
           "int", "str", "list", "dict", "self", "class", "const", "let", "var",
           "void", "enum", "func"]
_SHORT_LINES = [
    "PS C:\\> git status",
    "$ ls -la",
    "# TODO: fix this later",
    "const x = 42;",
    "return True",
    "print('hello')",
    "git commit -m",
    "npm install",
    "echo done",
    "已连接 Connected",
    "同步中 Syncing",
    "版本 v3.9.2",
    "状态: 已登录",
    "15:30 已同步",
    "error: not found",
]

_ZH_FONTS = ["Microsoft YaHei", "SimSun"]
_LATIN_FONTS = ["Segoe UI", "Arial"]
_MONO_FONTS = ["Consolas", "Cascadia Code", "Courier New"]

_LIGHT = ("#ffffff", "#222222")
_GRAY = ("#f5f5f5", "#333333")
_DARK = ("#1e1e1e", "#d4d4d4")
_SCHEMES = [_LIGHT, _GRAY, _DARK]

# Nominal real-world font sizes (CSS px at dpr=1; dpr=1.5 below renders them
# at the physical px height a real 150%-scaling screenshot would have).
#   UI labels / dialogs       -> 9pt  = 12px  -> 18px @1.5
#   terminal / chat / small   -> 12px        -> 18px @1.5
#   code editor               -> 14px        -> 21px @1.5
#   web body                  -> 16px        -> 24px @1.5
# Each content type draws from the font sizes its real app would use.
_TYPE_SPEC = [
    # (type, bank, fonts, [css font sizes])
    ("label_cjk", _CJK_LABELS, _ZH_FONTS, [12, 12, 14]),       # 9pt UI
    ("label_lat", _LATIN_LABELS, _LATIN_FONTS, [12, 12, 14]),
    ("value", _VALUES, _LATIN_FONTS, [12, 14, 16]),
    ("prompt", _PROMPTS, _MONO_FONTS, [12, 12, 14]),           # terminal 12pt
    ("token", _TOKENS, _MONO_FONTS, [14, 14, 16]),             # code 14px
    ("line", _SHORT_LINES, _MONO_FONTS, [12, 14, 16]),
]
DEVICE_SCALE = 1.5


def render_all(samples, out_dir):
    from playwright.sync_api import sync_playwright
    import cv2
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # dpr=1.5 -> every CSS px becomes 1.5 physical px, matching a real
        # 150%-scaling display screenshot.  Glyph heights land at 18-24px.
        ctx = browser.new_context(device_scale_factor=DEVICE_SCALE,
                                  viewport={"width": 800, "height": 600})
        page = ctx.new_page()
        for s in samples:
            cat_dir = out_dir / s["category"]
            cat_dir.mkdir(parents=True, exist_ok=True)
            html_path = cat_dir / f"{s['id']}.html"
            png_path = cat_dir / f"{s['id']}.png"
            bg, fg = s["scheme"]
            # white-space:pre so prompts/lines keep exact spacing.
            html = (f'<!DOCTYPE html><html><head><meta charset="UTF-8"><style>'
                    f'body{{margin:0;padding:8px 10px;background:{bg};color:{fg};'
                    f"font-family:'{s['font']}';font-size:{s['fs']}px;"
                    f'line-height:1.2;white-space:pre}}'
                    f"</style></head><body>{escape(s['content'])}</body></html>")
            html_path.write_text(html, encoding="utf-8")
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")
            raw_png = cat_dir / f"{s['id']}_raw.png"
            page.screenshot(path=str(raw_png), full_page=True)
            raw = cv2.imread(str(raw_png))
            if raw is None:
                raise RuntimeError(f"failed to read {raw_png}")
            gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
            bgpix = gray[0, 0]
            nonbg = np.abs(gray.astype(int) - int(bgpix)) > 24
            ys, xs = np.where(nonbg)
            if len(ys) == 0:
                crop = raw
                box = [raw.shape[1], raw.shape[0]]
            else:
                pad = 6
                y0 = max(0, int(ys.min()) - pad)
                y1 = min(raw.shape[0], int(ys.max()) + 1 + pad)
                x0 = max(0, int(xs.min()) - pad)
                x1 = min(raw.shape[1], int(xs.max()) + 1 + pad)
                crop = raw[y0:y1, x0:x1]
                box = [crop.shape[1], crop.shape[0]]
            cv2.imwrite(str(png_path), crop)
            raw_png.unlink(missing_ok=True)
            html_path.unlink(missing_ok=True)
            rendered.append({
                "id": s["id"], "category": s["category"],
                "truth": s["content"],
                "meta": {"png": str(png_path.relative_to(out_dir)),
                         "size": [int(box[0]), int(box[1])],
                         "font": s["font"], "font_size": s["fs"],
                         "type": s["type"]},
            })
        ctx.close()
        browser.close()
    return rendered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="scratch/crisp_small_dataset")
    ap.add_argument("--seed", type=int, default=20260724)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = _project_root / args.out

    samples = []
    idx = 0
    for typ, bank, fonts, fss in _TYPE_SPEC:
        for content in bank:
            fs = rng.choice(fss)
            samples.append({
                "id": f"{typ}_{idx:03d}",
                "category": typ,
                "type": typ,
                "content": content,
                "fs": fs,
                "font": rng.choice(fonts),
                "scheme": rng.choice(_SCHEMES),
            })
            idx += 1

    print(f"Rendering {len(samples)} crisp small samples to {out_dir} ...")
    print(f"  device_scale_factor={DEVICE_SCALE} (glyph heights ~18-24px physical)")
    rendered = render_all(samples, out_dir)
    (out_dir / "manifest.json").write_text(
        json.dumps(rendered, ensure_ascii=False, indent=2), encoding="utf-8")

    sizes = sorted(min(s["meta"]["size"]) for s in rendered)
    print(f"\nDone. {len(rendered)} images in {out_dir}")
    print(f"short side: min={sizes[0]} median={sizes[len(sizes)//2]} max={sizes[-1]}")
    from collections import Counter
    by_typ = Counter(s["meta"]["type"] for s in rendered)
    print("by type:", dict(by_typ))
    print("short-side histogram:")
    for lo, hi, lbl in [(0,25,"<25"),(25,35,"25-35"),(35,50,"35-50"),
                        (50,70,"50-70"),(70,100,"70-100"),(100,150,"100-150"),
                        (150,10**9,">150")]:
        n = sum(1 for ss in sizes if lo <= ss < hi)
        print(f"  {lbl:8s} {n:3d}  {'#'*n}")


if __name__ == "__main__":
    main()
