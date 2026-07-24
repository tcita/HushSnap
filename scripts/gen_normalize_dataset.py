"""Generate a statistically rich, REALISTIC desktop-screenshot dataset for OCR A/B testing.

Renders HTML -> Playwright Chromium (dpr=1.5) -> PNG, keeping ground-truth text.

This is the reliable general-purpose generator. Two fixes over the original
(dpr=1, font 12-24px) dataset, which was systematically ~1/3 UNDERSIZED vs
real screenshots on a 150%-scaling display (2560x1600 etc.):
  - device_scale_factor=1.5: a 9pt Windows UI label renders at 18px physical
    (not 12), a 12pt terminal prompt at 24px (not 16) - matching real
    screenshot glyph heights (18-30px).  See memory limit-side-len-736-wrong.
  - Font sizes = real nominal values per category, NOT an arbitrary spread.

DESIGN MATRIX (forced balancing, not random sampling).  These are treated as
independent variables and assigned by cartesian-product rotation so no
(category, ...) cell is empty at small n:
  - tier:   short / medium / long       (content length)
  - scheme: light / dark / gray        (ALL categories use all 3 - dark was
                                        missing from word/chat/ui/web; code
                                        was hard-coded dark.  Fixed.)
  - lang:   zh / en                     (stratified, not per-category p)
  - size_bin: tiny / body / large       (physical px tier, see below)
size_bin is NOT a global cartesian: each category uses only the bins that make
semantic sense for it (e.g. code has no tiny, chat has no large).  Only content
(which sentence, how many words) stays free-random.

SIZE_BIN (CSS px -> physical x1.5 at dpr=1.5).  The old set squeezed everything
into 18-30px physical (body only).  This set spans the full OCR-relevant range:
  tiny:  CSS 10-14px -> physical 15-21px  (UI labels/captions/small print)
  body:  CSS 14-18px -> physical 21-27px  (body text, old coverage)
  large: CSS 24-36px -> physical 36-54px  (headlines/emphatic numbers)
tiny and large are exactly where OCR is most error-prone and most needs a bench.

Coverage: 6 categories (word/chat/ui/code/web/terminal) x the matrix above,
fixed seed.  ~480 images -> every (cat,tier,scheme,lang,size_bin) cell has
>= floor(per_cat / n_cells) samples, never 0.

Chromium (not Qt) because the offscreen Qt platform has no font backend
(renders tofu); Chromium has its own, runs headless, gives truth for free.
Each PNG is pixel-cropped to the ink bounding box + small margin, mimicking a
user's "just enough to contain the text" screenshot.  Background is sampled at
all four corners (mode) so a non-blank top-left corner no longer mis-crops.

Output layout:
    {out}/manifest.json   # [{id, category, truth, meta:{png,size,font,font_size,
                          #            scheme,lang,size_tier,size_bin,
                          #            has_number,has_link,has_symbol}}]
    {category}/{id}.png
    {category}/{id}.truth.txt

Usage:
    python scripts/gen_normalize_dataset.py --n 480 --seed 20260724
    python scripts/gen_normalize_dataset.py --quick   # one per cell, smoke

NOTE: emoji were removed - in font-incomplete render environments they tofu
while truth.txt still holds them, creating false-negative samples.  HushSnap
screenshots for OCR don't need them.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent

# dpr=1.5 so CSS px -> 1.5x physical px (real 150%-scaling screenshot heights).
# Glyphs land at 15-54px physical across size_bins (tiny..large).
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
    "缓存命中率低于阈值时会触发自动刷新机制。",
    "请在网络稳定的环境下重试以避免数据丢失。",
    "日志文件默认保存在用户目录下的隐藏文件夹中。",
    "版本号采用语义化版本规范即主版本点次版本点修订号。",
    "批量导出时每页最多支持五十条记录且不可超过此上限。",
    "加密传输使用传输层安全协议确保数据不被窃听或篡改。",
    "勾选该选项后程序将以管理员权限请求运行。",
    "距离上次同步已过去三小时四十二分请检查网络连接。",
]
_ZH_WORDS = ["账户", "设置", "同步", "状态", "连接", "文件", "提示", "版本",
             "语言", "导出", "登录", "快捷键", "选项", "缓存", "文件夹", "确认",
             "刷新", "删除", "编辑", "复制", "粘贴", "撤销", "重置", "应用"]
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
    "The cache hit ratio below the threshold triggers an automatic refresh.",
    "Please retry on a stable network to avoid data loss.",
    "Log files are saved by default in a hidden folder under the user directory.",
    "Version numbers follow semantic versioning: major.minor.patch.",
    "Batch export supports up to fifty records per page and cannot exceed this limit.",
    "Encrypted transport uses TLS to ensure data is not intercepted or tampered with.",
    "Checking this option makes the program request administrator privileges.",
    "It has been three hours and forty-two minutes since the last sync; check your connection.",
]
_EN_WORDS = ["Account", "Settings", "Sync", "Status", "Connection", "Files",
             "Hint", "Version", "Language", "Export", "Login", "Hotkey",
             "Options", "Cache", "Folder", "Confirm", "Refresh", "Delete",
             "Edit", "Copy", "Paste", "Undo", "Reset", "Apply"]
_NUMBERS = ["1,234", "56,789", "0.95", "1024", "2026-07-24", "15:30",
            "+86 138", "3.14", "100%", "8080", "v3.9.2", "C:\\Users",
            "99.9%", "4096", "12ms", "TTL=56", "256MiB", "UTC+8"]
_LINKS = ["https://example.com/docs", "www.example.com", "example.com/path",
          "mailto:user@example.com", "github.com/user/repo",
          "https://docs.example.com/v3", "ftp://files.example.com"]

_ZH_FONTS = ["Microsoft YaHei", "SimSun", "SimHei", "KaiTi"]
_LATIN_FONTS = ["Segoe UI", "Arial", "Calibri", "Verdana"]
_MONO_FONTS = ["Consolas", "Cascadia Code", "Courier New"]

_LIGHT = ("#ffffff", "#222222", ["#0066cc", "#0a7d33", "#888888", "#c0392b"])
_DARK = ("#1e1e1e", "#d4d4d4", ["#569cd6", "#ce9178", "#6a9955", "#dcdcaa"])
_GRAY = ("#f5f5f5", "#333333", ["#0066cc", "#0a7d33", "#888888", "#c0392b"])
_SCHEMES = {"light": _LIGHT, "dark": _DARK, "gray": _GRAY}

# CSS font sizes per (category, size_bin).  dpr=1.5 -> physical x1.5.
#   tiny  10-14px CSS -> 15-21px physical (UI labels/captions)
#   body  14-18px CSS -> 21-27px physical (body text)
#   large 24-36px CSS -> 36-54px physical (headlines/emphatic)
_FONTSIZES = {
    "word":     {"tiny": [12, 14], "body": [16, 18], "large": [28, 32]},
    "chat":     {"tiny": [13],      "body": [15, 16]},
    "ui":       {"tiny": [10, 11], "body": [12, 13]},
    "code":     {                  "body": [14, 15], "large": [18, 20]},
    "web":      {                  "body": [15, 16], "large": [24, 28]},
    "terminal": {"tiny": [11, 12], "body": [13, 14]},
}

# Which size_bins each category uses (semantic - not all bins fit every cat).
_CAT_SIZEBINS = {
    "word": ["tiny", "body", "large"],
    "chat": ["tiny", "body"],
    "ui": ["tiny", "body"],
    "code": ["body", "large"],
    "web": ["body", "large"],
    "terminal": ["tiny", "body"],
}
_TIERS = ["short", "medium", "long"]
_SCHEME_KEYS = ["light", "dark", "gray"]
_LANGS = ["zh", "en"]


@dataclass
class Sample:
    id: str
    category: str
    html: str
    truth: str
    meta: dict = field(default_factory=dict)


def _pick(rng, bank, n): return [rng.choice(bank) for _ in range(n)]


def _bool_fields(truth: str) -> dict:
    """has_number/has_link/has_symbol - lets A/B slice by content without
    re-parsing truth."""
    has_number = bool(re.search(r"\d", truth))
    has_link = bool(re.search(r"(https?://|www\.|mailto:|\.com|\.org|github\.com)", truth))
    has_symbol = bool(re.search(r"[`~!@#$%^&*()_\-+={}\[\]|\\:;\"'<>?,./]", truth))
    return {"has_number": has_number, "has_link": has_link, "has_symbol": has_symbol}


def _fontsize_for(cat, size_bin, rng):
    return rng.choice(_FONTSIZES[cat][size_bin])


def _gen_word(rng, tier, scheme, lang, size_bin):
    fs = _fontsize_for("word", size_bin, rng)
    font = rng.choice(_ZH_FONTS + _LATIN_FONTS[:1])
    bg, fg, _ = _SCHEMES[scheme]
    zh = lang == "zh"
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
    meta = {"font": font, "font_size": fs, "scheme": scheme, "lang": lang,
            "size_bin": size_bin, **_bool_fields(truth)}
    return html, truth, meta


def _gen_chat(rng, tier, scheme, lang, size_bin):
    fs = _fontsize_for("chat", size_bin, rng)
    font = rng.choice(_ZH_FONTS[:2] + _LATIN_FONTS[:1])
    bg, fg, acc = _SCHEMES[scheme]
    senders = ["小明", "Alice", "李华", "Bob", "张三", "Carol", "王芳", "Dave"]
    if tier == "short": n = rng.randint(1, 2)
    elif tier == "medium": n = rng.randint(3, 4)
    else: n = rng.randint(5, 6)
    lines, msgs = [], []
    for _ in range(n):
        sender = rng.choice(senders)
        use_zh = lang == "zh"
        content = rng.choice((_ZH_SENTENCES if use_zh else _EN_SENTENCES)[:8])
        if rng.random() < 0.25: content += " " + rng.choice(_LINKS)
        if rng.random() < 0.25: content += " " + rng.choice(_NUMBERS)
        time = rng.choice(["09:15", "14:30", "昨天", "周一", ""])
        lines.append((sender, content, time))
        msgs.append(f"{sender}{' ' + time if time else ''}: {content}")
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
    meta = {"font": font, "font_size": fs, "scheme": scheme, "lang": lang,
            "size_bin": size_bin, **_bool_fields(truth)}
    return html, truth, meta


def _gen_ui(rng, tier, scheme, lang, size_bin):
    fs = _fontsize_for("ui", size_bin, rng)
    font = rng.choice(_LATIN_FONTS[:1] + _ZH_FONTS[:1])
    bg, fg, acc = _SCHEMES[scheme]
    pairs_zh = [("语言 / Language", "简体中文"), ("主题 / Theme", "浅色 Light"),
                ("快捷键 / Hotkey", "Ctrl+Shift+S"), ("开机自启", "是"),
                ("保存目录 / Output", "C:\\Users\\me\\Pictures"), ("同步频率", "每隔 15 分钟"),
                ("账户 / Account", "user@example.com"), ("状态 / Status", "已连接 Connected"),
                ("版本 / Version", "v3.9.2"), ("端口 / Port", "8080")]
    pairs_en = [("Language", "简体中文"), ("Theme", "Light"),
                ("Hotkey", "Ctrl+Shift+S"), ("Auto-start", "Yes"),
                ("Output folder", "C:\\Users\\me\\Pictures"), ("Sync interval", "Every 15 min"),
                ("Account", "user@example.com"), ("Status", "Connected"),
                ("Version", "v3.9.2"), ("Port", "8080")]
    is_zh = lang == "zh"
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
    meta = {"font": font, "font_size": fs, "scheme": scheme, "lang": lang,
            "size_bin": size_bin, **_bool_fields(truth)}
    return html, truth, meta


def _gen_code(rng, tier, scheme, _lang, size_bin):
    # _lang unused: code mixes CJK comments with English keywords, lang is
    # fixed "mix" (see _FIXED_LANG); param kept for signature uniformity.
    fs = _fontsize_for("code", size_bin, rng)
    font = rng.choice(_MONO_FONTS)
    bg, fg, _ = _SCHEMES[scheme]
    acc = _SCHEMES[scheme][2]
    # (token, color-accent-index-or-None) snippets.  8 diverse languages/styles.
    snippets = [
        # 0: Python def with CJK comment
        [("def", acc[0]), (" ", None), ("render_text", acc[3]), ("(", None), ("doc", acc[2]),
         (", ", None), ("width", acc[2]), ("):", None),
         ("\n    ", None), ("# 设置画布宽度并渲染文档", acc[2]),
         ("\n    ", None), ("doc", acc[2]), (".setTextWidth(", None), ("width", acc[2]), (")", None),
         ("\n    ", None), ("return", acc[2]), (" ", None), ("doc", acc[2]), (".toPlainText()", None)],
        # 1: Python if/for with JSON
        [("if", acc[0]), (" ", None), ("result", acc[2]), (".status == ", None), ("200", acc[1]),
         (":", None),
         ("\n    ", None), ("# 请求成功,解析返回的 JSON 数据", acc[2]),
         ("\n    ", None), ("data", acc[2]), (" = ", None), ("response", acc[2]), (".json()", None),
         ("\n    ", None), ("for", acc[0]), (" ", None), ("item", acc[2]), (" ", None), ("in", acc[0]),
         (" ", None), ("data", acc[2]), ("[", None), ("'items'", acc[1]), ("]:", None),
         ("\n        ", None), ("print", acc[3]), ("(", None), ("item", acc[2]), (".name)", None)],
        # 2: SQL query
        [("SELECT", acc[0]), (" id, name, created_at ", None), ("FROM", acc[0]), (" users", None),
         ("\n", None), ("WHERE", acc[0]), (" status = ", None), ("'active'", acc[1]),
         (" ", None), ("AND", acc[0]), (" created_at > ", None), ("'2026-01-01'", acc[1]),
         ("\n", None), ("-- 仅查询活跃用户", acc[2]),
         ("\n", None), ("ORDER BY", acc[0]), (" created_at ", None), ("DESC", acc[0]),
         (";", None)],
        # 3: JS async fetch
        [("async", acc[0]), (" ", None), ("function", acc[0]), (" ", None), ("fetchUser", acc[3]),
         ("(", None), ("id", acc[2]), (") {", None),
         ("\n  ", None), ("const", acc[0]), (" res = ", None), ("await", acc[0]),
         (" fetch(`", None), ("https://api.example.com/users/${id}", acc[1]), ("`);", None),
         ("\n  ", None), ("return", acc[2]), (" ", None), ("await", acc[0]), (" res.", None),
         ("json", acc[3]), ("();", None),
         ("\n}", None)],
        # 4: shell with PS prompt
        [("PS C:\\project> ", acc[0]), ("git", None), (" ", None), ("log", acc[2]),
         (" --oneline -5", None),
         ("\n", None), ("a1b2c3d", acc[1]), (" feat: add dark mode toggle", None),
         ("\n", None), ("e4f5g6h", acc[1]), (" fix: cache invalidation on sync", None),
         ("\n", None), ("# 记得在合并前跑测试", acc[2])],
        # 5: JSON config
        [("{", None),
         ("\n  ", None), ('"name"', acc[1]), (": ", None), ('"hushsnap"', acc[1]), (",", None),
         ("\n  ", None), ('"version"', acc[1]), (": ", None), ('"3.9.2"', acc[1]), (",", None),
         ("\n  ", None), ('"ocr"', acc[1]), (": {", None),
         ("\n    ", None), ('"engine"', acc[1]), (": ", None), ('"rapidocr"', acc[1]), (",", None),
         ("\n    ", None), ('"limit_side_len"', acc[1]), (": ", None), ("32", acc[3]),
         ("\n  }", None),
         ("\n}", None)],
        # 6: yaml config with CJK value
        [("server", acc[0]), (":", None),
         ("\n  ", None), ("host", acc[2]), (": ", None), ("0.0.0.0", acc[1]),
         ("\n  ", None), ("port", acc[2]), (": ", None), ("8080", acc[1]),
         ("\n  ", None), ("timeout", acc[2]), (": ", None), ("30s", acc[1]),
         ("\n", None), ("logging", acc[0]), (":", None),
         ("\n  ", None), ("level", acc[2]), (": ", None), ("info", acc[1]),
         ("\n  ", None), ("# 输出到文件和控制台", acc[2])],
        # 7: C-style with preprocessor
        [("#include", acc[0]), (" <stdio.h>", None),
         ("\n", None),
         ("\n", None), ("int", acc[0]), (" ", None), ("main", acc[3]), ("(", None),
         ("void", acc[2]), (") {", None),
         ("\n    ", None), ("printf", acc[3]), ("(", None), ('"count = %d\\n"', acc[1]),
         (", ", None), ("42", acc[3]), (");", None),
         ("\n    ", None), ("return", acc[2]), (" ", None), ("0", acc[3]), (";", None),
         ("\n}", None)],
    ]
    if tier == "short":
        blocks = [rng.choice(snippets)]
    elif tier == "medium":
        blocks = rng.sample(snippets, 2)
    else:
        blocks = rng.sample(snippets, min(3, len(snippets)))
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
    meta = {"font": font, "font_size": fs, "scheme": scheme, "lang": "mix",
            "size_bin": size_bin, **_bool_fields(truth)}
    return html, truth, meta


def _gen_web(rng, tier, scheme, lang, size_bin):
    fs = _fontsize_for("web", size_bin, rng)
    font = rng.choice(_ZH_FONTS + _LATIN_FONTS[:1])
    bg, fg, acc = _SCHEMES[scheme]
    zh = lang == "zh"
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
    meta = {"font": font, "font_size": fs, "scheme": scheme, "lang": lang,
            "size_bin": size_bin, **_bool_fields(truth)}
    return html, truth, meta


def _gen_terminal(rng, tier, scheme, _lang, size_bin):
    # _lang unused: terminal commands/paths are English, lang is fixed "en"
    # (see _FIXED_LANG); param kept for signature uniformity.
    fs = _fontsize_for("terminal", size_bin, rng)
    font = rng.choice(_MONO_FONTS)
    bg, fg, acc = _SCHEMES[scheme]
    prompts = [
        (f"PS C:\\Users\\me>", "python scripts/run.py --verbose"),
        (f"PS C:\\project>", "git status"),
        (f"user@host:~$", "ls -la /var/log"),
        (f"PS C:\\>", "ping example.com"),
        (f"admin@db:~$", "mysql -u root -p"),
    ]
    prompt, cmd = rng.choice(prompts)
    outputs = [
        ["Loading model... done", "Processing 3 files", "Output saved to ./out"],
        ["On branch master", "nothing to commit, working tree clean"],
        ["drwxr-xr-x 2 root root 4096 Jul 24 logs", "-rw-r--r-- 1 root root 1024 Jul 24 app.log"],
        ["Pinging example.com with 32 bytes of data:", "Reply from 93.184.216.34: bytes=32 time=12ms TTL=56"],
        ["Welcome to the MySQL monitor.  Commands end with ; or \\g.", "Type 'help;' or '\\h' for help."],
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
    meta = {"font": font, "font_size": fs, "scheme": scheme, "lang": "en",
            "size_bin": size_bin, **_bool_fields(truth)}
    return html, truth, meta


CATEGORIES = {
    "word": _gen_word, "chat": _gen_chat, "ui": _gen_ui,
    "code": _gen_code, "web": _gen_web, "terminal": _gen_terminal,
}


# Categories whose language is FIXED (not a matrix variable).  code mixes CJK
# comments with English keywords; terminal output is English commands.  They
# don't participate in the lang rotation - their lang cell is a single value.
_FIXED_LANG = {"code": "mix", "terminal": "en"}


def _cells_for(cat):
    """Cartesian product of (tier, scheme, lang, size_bin) for a category.
    lang is rotated only for categories with a real zh/en split; code/terminal
    use a single fixed lang (their language doesn't vary with the matrix)."""
    bins = _CAT_SIZEBINS[cat]
    langs = [_FIXED_LANG[cat]] if cat in _FIXED_LANG else _LANGS
    cells = []
    for tier in _TIERS:
        for scheme in _SCHEME_KEYS:
            for lang in langs:
                for sb in bins:
                    cells.append((tier, scheme, lang, sb))
    return cells


def _build_samples(rng, per_cat, quick):
    """Allocate samples across cells with balanced distribution.  Each cell
    gets per_cat // n_cells samples; the remainder is spread over RANDOMLY
    chosen cells (seeded shuffle) so no tier/scheme/lang/bin is systematically
    over- or under-sampled.  quick -> 1 per cell."""
    samples = []
    for cat, gen in CATEGORIES.items():
        cells = _cells_for(cat)
        if quick:
            counts = [1] * len(cells)
        else:
            base = per_cat // len(cells)
            rem = per_cat - base * len(cells)
            # shuffle cells (seeded) then give the first `rem` an extra sample,
            # so the remainder is spread uniformly across tier/scheme/lang/bin
            # instead of clustering on the first-generated cells.
            order = cells[:]
            rng.shuffle(order)
            extra = set(order[:rem])
            counts = [base + (1 if c in extra else 0) for c in cells]
        idx = 0
        for (tier, scheme, lang, sb), cnt in zip(cells, counts):
            for _ in range(cnt):
                html, truth, meta = gen(rng, tier, scheme, lang, sb)
                meta["size_tier"] = tier
                samples.append(Sample(
                    id=f"{cat}_{idx:03d}", category=cat, html=html,
                    truth=truth, meta=meta,
                ))
                idx += 1
    return samples


def render_all(samples, out_dir):
    from playwright.sync_api import sync_playwright
    import cv2
    import numpy as np
    from collections import Counter

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # dpr=1.5 -> CSS px become 1.5x physical px, matching a real 150%-
        # scaling screenshot.  Glyph heights land at 15-54px across size_bins.
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
            # Background = mode of the 4 corners (not just top-left), so a
            # non-blank top-left (content/gradient) no longer mis-crops.
            corners = [int(gray[0, 0]), int(gray[0, -1]),
                       int(gray[-1, 0]), int(gray[-1, -1])]
            bgpix = Counter(corners).most_common(1)[0][0]
            nonbg = np.abs(gray.astype(int) - bgpix) > 24
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
    ap.add_argument("--quick", action="store_true", help="one per cell (smoke)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = _project_root / args.out

    cats = list(CATEGORIES.keys())
    per_cat = args.n // len(cats) if not args.quick else 0
    samples = _build_samples(rng, per_cat, args.quick)

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
    by_scheme = Counter(s.meta.get("scheme") for s in rendered)
    by_lang = Counter(s.meta.get("lang") for s in rendered)
    by_bin = Counter(s.meta.get("size_bin") for s in rendered)
    sizes = [min(s.meta.get("size", [0, 0])) for s in rendered]
    sizes.sort()
    print(f"\nDone. {len(rendered)} images in {out_dir}")
    print("By category:", dict(by_cat))
    print("By tier:    ", dict(by_tier))
    print("By scheme:  ", dict(by_scheme))
    print("By lang:    ", dict(by_lang))
    print("By size_bin:", dict(by_bin))
    if sizes:
        print(f"short side: min={sizes[0]} median={sizes[len(sizes)//2]} max={sizes[-1]}")
        print("short-side histogram:")
        for lo, hi, lbl in [(0,50,"<50"),(50,100,"50-100"),(100,200,"100-200"),
                            (200,400,"200-400"),(400,10**9,">400")]:
            n = sum(1 for ss in sizes if lo <= ss < hi)
            print(f"  {lbl:8s} {n:3d}  {'#'*n}")


if __name__ == "__main__":
    main()
