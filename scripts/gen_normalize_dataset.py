"""Generate a statistically rich, REALISTIC desktop-screenshot dataset for OCR A/B testing.

Renders HTML -> Playwright Chromium (dpr=1.5) -> PNG, keeping ground-truth text.

This is the reliable general-purpose generator. Two fixes over the original
(dpr=1, font 12-24px) dataset, which was systematically ~1/3 UNDERSIZED vs
real screenshots on a 150%-scaling display (2560x1600 etc.):
  - device_scale_factor=1.5: a 9pt Windows UI label renders at 18px physical
    (not 12), a 12pt terminal prompt at 24px (not 16) - matching real
    screenshot glyph heights (18-30px).  See memory limit-side-len-736-wrong.
  - Font sizes = real nominal values per category (not an arbitrary spread).
  - Content-size tiers (short/medium/long) per category so the dataset spans
    SMALL (single-line crops, ~25-50px short side) to LARGE (multi-paragraph,
    200+px) - not just one size.  A user screenshots a button OR a whole doc.

Coverage: 6 categories (word/chat/ui/code/web/terminal) x 3 size tiers x
CJK/Latin x light/dark/gray, fixed seed.  ~480 images -> enough for paired A/B
and bucketed-by-category/size/lang analysis with ~10+ per cell.

Chromium (not Qt) because the offscreen Qt platform has no font backend
(renders tofu); Chromium has its own, runs headless, gives truth for free.
Each PNG is pixel-cropped to the ink bounding box + small margin, mimicking a
user's "just enough to contain the text" screenshot.

Output layout:
    {out}/manifest.json   # [{id, category, truth, meta:{png,size,font,font_size,scheme,size_tier,lang}}]
    {category}/{id}.png
    {category}/{id}.truth.txt

Usage:
    python scripts/gen_normalize_dataset.py --n 480 --seed 20260724
    python scripts/gen_normalize_dataset.py --quick   # one per category*size, smoke
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent

# dpr=1.5 so CSS px -> 1.5x physical px (real 150%-scaling screenshot heights).
# Glyphs land at 18-30px physical instead of the dpr=1 12-20px (too small).
DEVICE_SCALE = 1.5

# ── Content banks ────────────────────────────────────────────────────────────
_ZH_SENTENCES = [
    "检测模型的归一化参数对识别准确率的影响在多数场景下并不显著。",
    "实验表明当输入图像的短边小于阈值时检测器仍会降低特征质量。",
    "在此情况下纯识别回退路径通常能获得更好的整体结果。",
    "垂直排列的中日韩文字无需旋转即可被模型正确识别。",
    "用户可以在设置中修改快捷键以及开机自启动等选项。",
    "已同步的文件会保存在本地缓存中以便离线访问。",
    "请注意修改账户信息后需要重新登录以使其生效。",
    "系统检测到新版本可用是否立即下载并安装。",
    "该项目目前支持简体中文繁体中文和日语三种语言。",
    "导出完成后将自动打开所在文件夹便于查看。",
]
_ZH_WORDS = ["账户", "设置", "同步", "状态", "连接", "文件", "提示", "版本",
             "语言", "导出", "登录", "快捷键", "选项", "缓存", "文件夹", "确认"]
_EN_SENTENCES = [
    "The normalization parameters of the detection model have little effect on accuracy in most cases.",
    "Experiments show that when the short side of the input image is below the threshold, the detector still degrades feature quality.",
    "In this situation, the recognition-only fallback path usually yields better overall results.",
    "Vertical CJK text can be recognized correctly by the model without rotation.",
    "Users can modify hotkeys and startup options in the settings.",
    "Synced files are saved in the local cache for offline access.",
    "Please note that you need to log in again after changing your account information.",
    "The system detected that a new version is available.",
    "The project currently supports Simplified Chinese, Traditional Chinese, and Japanese.",
    "After export is complete, the containing folder will open automatically.",
]
_EN_WORDS = ["Account", "Settings", "Sync", "Status", "Connection", "Files",
             "Hint", "Version", "Language", "Export", "Login", "Hotkey",
             "Options", "Cache", "Folder", "Confirm"]
_NUMBERS = ["1,234", "56,789", "0.95", "1024", "2026-07-24", "15:30",
            "+86 138", "3.14", "100%", "8080", "v3.9.2", "C:\\Users"]
_EMOJI = ["😊", "👍", "🎉", "❤️", "😂", "🔥", "✅", "⏰"]
_LINKS = ["https://example.com/docs", "www.example.com", "example.com/path",
          "mailto:user@example.com", "github.com/user/repo"]

_ZH_FONTS = ["Microsoft YaHei", "SimSun", "SimHei", "KaiTi"]
_LATIN_FONTS = ["Segoe UI", "Arial", "Calibri", "Verdana"]
_MONO_FONTS = ["Consolas", "Cascadia Code", "Courier New"]

_LIGHT = ("#ffffff", "#222222", ["#0066cc", "#0a7d33", "#888888", "#c0392b"])
_DARK = ("#1e1e1e", "#d4d4d4", ["#569cd6", "#ce9178", "#6a9955", "#dcdcaa"])
_GRAY = ("#f5f5f5", "#333333", ["#0066cc", "#0a7d33", "#888888"])

# Real nominal CSS font sizes per category (dpr=1.5 -> physical x1.5):
#   UI label 9-10pt=12-14, terminal/chat 12pt=12-14, code 14-16, web/body 15-18.
_FONTSIZES = {
    "word": [16, 18, 20], "chat": [14, 15, 16], "ui": [12, 13, 14],
    "code": [14, 15, 16], "web": [15, 16, 18], "terminal": [12, 13, 14],
}
_TIERS = ["short", "medium", "long"]


@dataclass
class Sample:
    id: str
    category: str
    html: str
    truth: str
    meta: dict = field(default_factory=dict)


def _pick(rng, bank, n): return [rng.choice(bank) for _ in range(n)]


def _gen_word(rng, tier):
    fs = rng.choice(_FONTSIZES["word"])
    font = rng.choice(_ZH_FONTS + _LATIN_FONTS[:1])
    bg, fg, _ = rng.choice([_LIGHT, _GRAY])
    zh = rng.random() < 0.6
    bank = _ZH_SENTENCES if zh else _EN_SENTENCES
    if tier == "short":
        paras = [" ".join(_pick(rng, bank, 1))]
    elif tier == "medium":
        paras = [" ".join(_pick(rng, bank, rng.randint(1, 2))) for _ in range(2)]
    else:
        paras = [" ".join(_pick(rng, bank, rng.randint(2, 4))) for _ in range(rng.randint(3, 4))]
    truth = "\n\n".join(paras)
    body = "".join(f"<p>{escape(p)}</p>" for p in paras)
    lh = round(fs * 1.6, 1)
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{bg};color:{fg};font-family:'{font}',sans-serif;
font-size:{fs}px;line-height:{lh}px;padding:8px 10px;max-width:680px}}
p{{margin:0 0 {fs}px 0}}
</style></head><body>{body}</body></html>"""
    return html, truth, {"font": font, "font_size": fs, "scheme": "light" if bg == "#ffffff" else "gray", "lang": "zh" if zh else "en"}


def _gen_chat(rng, tier):
    fs = rng.choice(_FONTSIZES["chat"])
    font = rng.choice(_ZH_FONTS[:2] + _LATIN_FONTS[:1])
    bg, fg, acc = rng.choice([_LIGHT, _GRAY])
    senders = ["小明", "Alice", "李华", "Bob", "张三", "Carol"]
    if tier == "short": n = rng.randint(1, 2)
    elif tier == "medium": n = rng.randint(3, 4)
    else: n = rng.randint(5, 6)
    lines, msgs = [], []
    zh_count = en_count = 0
    for _ in range(n):
        sender = rng.choice(senders)
        use_zh = rng.random() < 0.55
        content = rng.choice((_ZH_SENTENCES if use_zh else _EN_SENTENCES)[:6])
        if rng.random() < 0.3: content += " " + rng.choice(_EMOJI)
        if rng.random() < 0.2: content += " " + rng.choice(_LINKS)
        if rng.random() < 0.2: content += " " + rng.choice(_NUMBERS)
        time = rng.choice(["09:15", "14:30", "昨天", "周一", ""])
        lines.append((sender, content, time))
        msgs.append(f"{sender}{' ' + time if time else ''}: {content}")
        if use_zh: zh_count += 1
        else: en_count += 1
    truth = "\n".join(msgs)
    rows = ""
    for sender, content, time in lines:
        t = f'<span class="t">{escape(time)}</span>' if time else ""
        rows += (f'<div class="msg"><span class="s" style="color:{acc[0]}">{escape(sender)}</span>'
                 f'{t}<span class="c">{escape(content)}</span></div>')
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{bg};color:{fg};font-family:'{font}',sans-serif;font-size:{fs}px;padding:8px 10px;width:420px}}
.msg{{margin:0 0 {fs//2}px 0;line-height:1.5}}
.s{{font-weight:bold;margin-right:6px}}
.t{{color:{acc[2]};font-size:{fs-2}px;margin-right:6px}}
.c{{}}
</style></head><body>{rows}</body></html>"""
    lang = "zh" if zh_count >= en_count else "en"
    return html, truth, {"font": font, "font_size": fs, "scheme": "light" if bg == "#ffffff" else "gray", "lang": lang}


def _gen_ui(rng, tier):
    fs = rng.choice(_FONTSIZES["ui"])
    font = rng.choice(_LATIN_FONTS[:1] + _ZH_FONTS[:1])
    bg, fg, acc = rng.choice([_LIGHT, _GRAY])
    pairs_zh = [("语言 / Language", "简体中文"), ("主题 / Theme", "浅色 Light"),
                ("快捷键 / Hotkey", "Ctrl+Shift+S"), ("开机自启", "是"),
                ("保存目录 / Output", "C:\\Users\\me\\Pictures"), ("同步频率", "每隔 15 分钟"),
                ("账户 / Account", "user@example.com"), ("状态 / Status", "已连接 Connected")]
    pairs_en = [("Language", "简体中文"), ("Theme", "Light"),
                ("Hotkey", "Ctrl+Shift+S"), ("Auto-start", "Yes"),
                ("Output folder", "C:\\Users\\me\\Pictures"), ("Sync interval", "Every 15 min"),
                ("Account", "user@example.com"), ("Status", "Connected")]
    is_zh = rng.random() < 0.5
    pairs = pairs_zh if is_zh else pairs_en
    if tier == "short": n = rng.randint(1, 2)
    elif tier == "medium": n = rng.randint(3, 4)
    else: n = rng.randint(5, 8)
    chosen = rng.sample(pairs, min(n, len(pairs)))
    truth = "\n".join(f"{k}: {v}" for k, v in chosen)
    rows = "".join(
        f'<div class="row"><span class="k">{escape(k)}</span>'
        f'<span class="sep">:</span><b class="v">{escape(v)}</b></div>'
        for k, v in chosen)
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{bg};color:{fg};font-family:'{font}',sans-serif;font-size:{fs}px;padding:8px 12px}}
.row{{margin:0 0 {fs//2}px 0;line-height:1.6}}
.k{{color:{acc[2]}}}.sep{{margin:0 4px}}.v{{color:{acc[0]};font-family:Consolas,monospace}}
</style></head><body>{rows}</body></html>"""
    return html, truth, {"font": font, "font_size": fs, "scheme": "light" if bg == "#ffffff" else "gray", "lang": "zh" if is_zh else "en"}


def _gen_code(rng, tier):
    fs = rng.choice(_FONTSIZES["code"])
    font = rng.choice(_MONO_FONTS)
    bg, fg, _ = _DARK
    acc = _DARK[2]
    snippets = [
        [("def", acc[0]), (" ", None), ("render_text", acc[3]), ("(", None), ("doc", acc[2]),
         (", ", None), ("width", acc[2]), ("):", None),
         ("\n    ", None), ("# 设置画布宽度并渲染文档", acc[2]),
         ("\n    ", None), ("doc", acc[2]), (".setTextWidth(", None), ("width", acc[2]), (")", None),
         ("\n    ", None), ("return", acc[2]), (" ", None), ("doc", acc[2]), (".toPlainText()", None)],
        [("if", acc[0]), (" ", None), ("result", acc[2]), (".status == ", None), ("200", acc[1]),
         (":", None),
         ("\n    ", None), ("# 请求成功,解析返回的 JSON 数据", acc[2]),
         ("\n    ", None), ("data", acc[2]), (" = ", None), ("response", acc[2]), (".json()", None),
         ("\n    ", None), ("for", acc[0]), (" ", None), ("item", acc[2]), (" ", None), ("in", acc[0]),
         (" ", None), ("data", acc[2]), ("[", None), ("'items'", acc[1]), ("]:", None),
         ("\n        ", None), ("print", acc[3]), ("(", None), ("item", acc[2]), (".name)", None)],
        [("SELECT", acc[0]), (" id, name, created_at ", None), ("FROM", acc[0]), (" users", None),
         ("\n", None), ("WHERE", acc[0]), (" status = ", None), ("'active'", acc[1]),
         (" ", None), ("AND", acc[0]), (" created_at > ", None), ("'2026-01-01'", acc[1]),
         ("\n", None), ("-- 仅查询活跃用户", acc[2]),
         ("\n", None), ("ORDER BY", acc[0]), (" created_at ", None), ("DESC", acc[0]),
         (";", None)],
    ]
    if tier == "short":
        blocks = [rng.choice(snippets)]
    elif tier == "medium":
        blocks = rng.sample(snippets, 2)
    else:
        blocks = rng.sample(snippets * 2, 3)
    truth = "".join("".join(t for t, _ in b) for b in blocks)
    body = ""
    for b in blocks:
        for text, color in b:
            seg = f'<span style="color:{color}">{escape(text)}</span>' if color else escape(text)
            body += seg
        body += "\n"
    body = f'<pre style="margin:0">{body.replace(chr(10), "<br>")}</pre>'
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{bg};color:{fg};font-family:'{font}',monospace;font-size:{fs}px;line-height:1.5;padding:8px 10px}}
pre{{white-space:pre;font-family:'{font}',monospace}}
</style></head><body>{body}</body></html>"""
    return html, truth, {"font": font, "font_size": fs, "scheme": "dark", "lang": "mix"}


def _gen_web(rng, tier):
    fs = rng.choice(_FONTSIZES["web"])
    font = rng.choice(_ZH_FONTS + _LATIN_FONTS[:1])
    bg, fg, acc = rng.choice([_LIGHT, _GRAY])
    zh = rng.random() < 0.6
    title = rng.choice(_ZH_SENTENCES if zh else _EN_SENTENCES)[:1][0].split("。")[0] \
        if zh else rng.choice(_EN_SENTENCES).split(".")[0]
    body_sent = rng.choice(_ZH_SENTENCES if zh else _EN_SENTENCES)
    items = _pick(rng, (_ZH_WORDS if zh else _EN_WORDS), rng.randint(3, 4))
    if tier == "short":
        truth = f"{title}\n{body_sent}"
        body_html = f"<h1>{escape(title)}</h1><p>{escape(body_sent)}</p>"
    elif tier == "medium":
        truth = f"{title}\n{body_sent}\n" + "\n".join(f"- {it}" for it in items)
        body_html = (f"<h1>{escape(title)}</h1><p>{escape(body_sent)}</p>"
                     f"<ul>{''.join(f'<li>{escape(it)}</li>' for it in items)}</ul>")
    else:
        extra = _pick(rng, (_ZH_SENTENCES if zh else _EN_SENTENCES), 2)
        truth = f"{title}\n{body_sent}\n" + "\n".join(f"- {it}" for it in items) + "\n" + "\n".join(extra)
        body_html = (f"<h1>{escape(title)}</h1><p>{escape(body_sent)}</p>"
                     f"<ul>{''.join(f'<li>{escape(it)}</li>' for it in items)}</ul>"
                     f"{''.join(f'<p>{escape(e)}</p>' for e in extra)}")
    title_fs = fs + 6
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{bg};color:{fg};font-family:'{font}',sans-serif;font-size:{fs}px;line-height:1.6;padding:8px 10px;max-width:600px}}
h1{{font-size:{title_fs}px;margin:0 0 {fs}px 0;color:{acc[0]}}}
p{{margin:0 0 {fs}px 0}}
ul{{margin:0 0 0 18px}}li{{margin:0 0 4px 0}}
</style></head><body>{body_html}</body></html>"""
    return html, truth, {"font": font, "font_size": fs, "scheme": "light" if bg == "#ffffff" else "gray", "lang": "zh" if zh else "en"}


def _gen_terminal(rng, tier):
    fs = rng.choice(_FONTSIZES["terminal"])
    font = rng.choice(_MONO_FONTS)
    use_dark = rng.random() < 0.5
    bg, fg, acc = _DARK if use_dark else _GRAY
    prompts = [
        (f"PS C:\\Users\\me>", "python scripts/run.py --verbose"),
        (f"PS C:\\project>", "git status"),
        (f"user@host:~$", "ls -la /var/log"),
        (f"PS C:\\>", "ping example.com"),
    ]
    prompt, cmd = rng.choice(prompts)
    outputs = [
        ["Loading model... done", "Processing 3 files", "Output saved to ./out"],
        ["On branch master", "nothing to commit, working tree clean"],
        ["drwxr-xr-x 2 root root 4096 Jul 24 logs", "-rw-r--r-- 1 root root 1024 Jul 24 app.log"],
        ["Pinging example.com with 32 bytes of data:", "Reply from 93.184.216.34: bytes=32 time=12ms TTL=56"],
    ]
    pool = rng.choice(outputs)
    if tier == "short": out_lines = pool[:1]
    elif tier == "medium": out_lines = pool[:2]
    else: out_lines = pool
    truth = "\n".join([prompt + " " + cmd] + out_lines)
    rows = (f'<div><span style="color:{acc[0]}">{escape(prompt)}</span> '
            f'<span style="color:{fg}">{escape(cmd)}</span></div>')
    for ol in out_lines:
        rows += f'<div style="color:{fg}">{escape(ol)}</div>'
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:{bg};color:{fg};font-family:'{font}',monospace;font-size:{fs}px;line-height:1.5;padding:8px 10px}}
div{{white-space:pre}}
</style></head><body>{rows}</body></html>"""
    return html, truth, {"font": font, "font_size": fs, "scheme": "dark" if use_dark else "gray", "lang": "en"}


CATEGORIES = {
    "word": _gen_word, "chat": _gen_chat, "ui": _gen_ui,
    "code": _gen_code, "web": _gen_web, "terminal": _gen_terminal,
}


def render_all(samples, out_dir):
    from playwright.sync_api import sync_playwright
    import cv2
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # dpr=1.5 -> CSS px become 1.5x physical px, matching a real 150%-
        # scaling screenshot.  Glyph heights land at 18-30px (real range),
        # not 12-20px (dpr=1, too small).
        ctx = browser.new_context(device_scale_factor=DEVICE_SCALE,
                                  viewport={"width": 800, "height": 600})
        page = ctx.new_page()
        for s in samples:
            cat_dir = out_dir / s.category
            cat_dir.mkdir(parents=True, exist_ok=True)
            html_path = cat_dir / f"{s.id}.html"
            png_path = cat_dir / f"{s.id}.png"
            truth_path = cat_dir / f"{s.id}.truth.txt"
            body_rule = "body{width:680px}"
            html = s.html.replace("</style>", body_rule + "</style>", 1)
            html_path.write_text(html, encoding="utf-8")
            page.goto(f"file:///{html_path.as_posix()}")
            page.wait_for_load_state("networkidle")
            raw_png = cat_dir / f"{s.id}_raw.png"
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
                pad = 8
                y0 = max(0, int(ys.min()) - pad)
                y1 = min(raw.shape[0], int(ys.max()) + 1 + pad)
                x0 = max(0, int(xs.min()) - pad)
                x1 = min(raw.shape[1], int(xs.max()) + 1 + pad)
                crop = raw[y0:y1, x0:x1]
                box = [crop.shape[1], crop.shape[0]]
            cv2.imwrite(str(png_path), crop)
            raw_png.unlink(missing_ok=True)
            truth_path.write_text(s.truth, encoding="utf-8")
            html_path.unlink(missing_ok=True)
            rendered.append(Sample(
                id=s.id, category=s.category, html="", truth=s.truth,
                meta={**s.meta, "png": str(png_path.relative_to(out_dir)),
                      "truth": str(truth_path.relative_to(out_dir)),
                      "size": [int(box[0]), int(box[1])],
                      "size_tier": s.meta.get("size_tier", "")},
            ))
        ctx.close()
        browser.close()
    return rendered


def main():
    ap = argparse.ArgumentParser(description="Generate a realistic desktop-text OCR dataset.")
    ap.add_argument("--n", type=int, default=480, help="total images (default 480)")
    ap.add_argument("--seed", type=int, default=20260724, help="RNG seed for reproducibility")
    ap.add_argument("--out", type=str, default="scratch/desktop_dataset")
    ap.add_argument("--quick", action="store_true", help="one per category*size tier (smoke)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = _project_root / args.out

    if args.quick:
        samples = []
        for cat, gen in CATEGORIES.items():
            for tier in _TIERS:
                html, truth, meta = gen(rng, tier)
                meta["size_tier"] = tier
                samples.append(Sample(id=f"{cat}_{tier}", category=cat, html=html,
                                      truth=truth, meta=meta))
    else:
        cats = list(CATEGORIES.keys())
        per_cat = args.n // len(cats)
        samples = []
        for cat in cats:
            gen = CATEGORIES[cat]
            for i in range(per_cat):
                tier = _TIERS[i % len(_TIERS)]  # exact balance across tiers
                html, truth, meta = gen(rng, tier)
                meta["size_tier"] = tier
                samples.append(Sample(
                    id=f"{cat}_{i:03d}", category=cat, html=html, truth=truth, meta=meta,
                ))

    print(f"Rendering {len(samples)} samples (dpr={DEVICE_SCALE}) to {out_dir} ...")
    rendered = render_all(samples, out_dir)

    manifest = [{
        "id": s.id, "category": s.category, "truth": s.truth, "meta": s.meta,
    } for s in rendered]
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    by_cat = Counter(s.category for s in rendered)
    by_tier = Counter(s.meta.get("size_tier") for s in rendered)
    sizes = [min(s.meta.get("size", [0, 0])) for s in rendered]
    sizes.sort()
    print(f"\nDone. {len(rendered)} images in {out_dir}")
    print("By category:", dict(by_cat))
    print("By size tier:", dict(by_tier))
    if sizes:
        print(f"short side: min={sizes[0]} median={sizes[len(sizes)//2]} max={sizes[-1]}")
        print("short-side histogram:")
        for lo, hi, lbl in [(0,50,"<50"),(50,100,"50-100"),(100,200,"100-200"),
                            (200,400,"200-400"),(400,10**9,">400")]:
            n = sum(1 for ss in sizes if lo <= ss < hi)
            print(f"  {lbl:8s} {n:3d}  {'#'*n}")


if __name__ == "__main__":
    main()
