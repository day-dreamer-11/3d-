"""把 docs/项目报告书.md 转为可打印 HTML，并可选生成 PDF。

用法：
    python tools/build_report.py
    python tools/build_report.py --pdf

HTML 使用 A4 打印样式，可直接在浏览器中“打印 → 另存为 PDF”；
--pdf 会用本机 Edge/Chrome 的 headless 模式直接生成 PDF。
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "项目报告书.md"
HTML_PATH = ROOT / "docs" / "项目报告书.html"
PDF_PATH = ROOT / "docs" / "项目报告书.pdf"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

CSS = r"""
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
  color: #1f2d3a; font-size: 10.5pt; line-height: 1.75;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.cover-inner {
  min-height: 235mm; display: flex; flex-direction: column; justify-content: center;
  padding: 10mm 4mm;
}
.page-break { page-break-after: always; break-after: page; height: 0; }
.cover .band { height: 10mm; background: linear-gradient(90deg,#16324f,#2a7f8f); border-radius: 3mm; margin-bottom: 16mm; }
.cover h1 { font-size: 30pt; letter-spacing: 1px; color: #16324f; margin: 0 0 4mm 0; line-height: 1.3; }
.cover .sub { font-size: 14pt; color: #2a7f8f; margin-bottom: 18mm; }
.cover .meta { font-size: 10pt; color: #455a6b; line-height: 2; border-left: 3px solid #2a7f8f; padding-left: 5mm; }
.cover .note { margin-top: 16mm; font-size: 9pt; color: #6a7f92; }
.toc { page-break-after: always; }
.toc h2 { page-break-before: avoid; border: 0; padding: 0; margin: 0 0 6mm 0; font-size: 18pt; color: #16324f; }
.toc ol { list-style: none; padding: 0; margin: 0; counter-reset: toc; }
.toc li { margin: 2.6mm 0; padding-bottom: 1.6mm; border-bottom: 1px dotted #c9d6df; }
.toc a { color: #1f4e6b; text-decoration: none; }
.toc .num { display: inline-block; width: 9mm; color: #2a7f8f; font-weight: 600; }
h2 {
  page-break-before: always; font-size: 17pt; color: #16324f;
  border-bottom: 2px solid #2a7f8f; padding-bottom: 2.5mm; margin: 0 0 5mm 0;
}
h2:first-of-type { page-break-before: auto; }
h3 { font-size: 12.5pt; color: #1f4e6b; margin: 6mm 0 2.5mm 0; }
h4 { font-size: 11pt; color: #27405a; margin: 4mm 0 2mm 0; }
p { margin: 0 0 3mm 0; text-align: justify; }
strong { color: #16324f; }
code { font-family: Consolas, "Courier New", monospace; background: #eef4f8; padding: 0.3mm 1.2mm; border-radius: 1mm; font-size: 9.2pt; }
pre { background: #f4f8fb; border: 1px solid #d9e5ec; border-left: 3px solid #2a7f8f;
  border-radius: 1.5mm; padding: 3mm 4mm; overflow-x: auto; page-break-inside: avoid; }
pre code { background: transparent; padding: 0; font-size: 8.6pt; line-height: 1.55; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm 0; font-size: 9pt; page-break-inside: avoid; }
thead th { background: #eaf4f7; color: #16324f; font-weight: 600; }
th, td { border: 1px solid #cfdde6; padding: 1.8mm 2.4mm; text-align: left; vertical-align: top; }
tbody tr:nth-child(even) { background: #f8fbfd; }
blockquote { margin: 3mm 0; padding: 2.5mm 4mm; background: #f3f8fb; border-left: 3px solid #c08a3e; color: #455a6b; }
ul, ol { margin: 0 0 3mm 0; padding-left: 7mm; }
li { margin: 1.2mm 0; }
hr { border: 0; border-top: 1px solid #d9e5ec; margin: 6mm 0; }
a { color: #1f5b8a; }
.small { font-size: 8.5pt; color: #6a7f92; }
"""


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def smart_join(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if not out:
            out = part
            continue
        if out and part and ord(out[-1]) > 127 and ord(part[0]) > 127:
            out += part
        else:
            out += " " + part
    return out


def slug(index: int) -> str:
    return f"sec-{index}"


def parse_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c or "") for c in rows[1]):
        head, body = rows[0], rows[2:]
    else:
        head, body = rows[0], rows[1:]
    out = ["<table><thead><tr>"]
    out.extend(f"<th>{inline(c)}</th>" for c in head)
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def convert(md: str) -> tuple[str, list[tuple[str, str]]]:
    lines = md.splitlines()
    # 跳过封面区（标题、副标题、元信息、声明、首条分隔线），正文从“摘要”开始
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            start = i + 1
            break
    lines = lines[start:]
    body: list[str] = []
    toc: list[tuple[str, str]] = []
    headings = 0
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            body.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 2:
                headings += 1
                anchor = slug(headings)
                toc.append((text, anchor))
                body.append(f'<h2 id="{anchor}">{inline(text)}</h2>')
            elif level == 3:
                body.append(f"<h3>{inline(text)}</h3>")
            elif level == 4:
                body.append(f"<h4>{inline(text)}</h4>")
            else:
                body.append(f"<h1>{inline(text)}</h1>")
            i += 1
            continue
        if line.strip() == "---":
            body.append("<hr>")
            i += 1
            continue
        if line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            body.append(parse_table(table_lines))
            continue
        if line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip(">").strip())
                i += 1
            body.append("<blockquote>" + smart_join([inline(q) for q in quote]) + "</blockquote>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            body.append("<ul>" + "".join(f"<li>{inline(it)}</li>" for it in items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            body.append("<ol>" + "".join(f"<li>{inline(it)}</li>" for it in items) + "</ol>")
            continue
        para = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip() or re.match(r"^(#{1,4})\s+", nxt) or nxt.startswith("```") \
                    or nxt.startswith("|") or nxt.startswith(">") or nxt.strip() == "---" \
                    or re.match(r"^\s*[-*]\s+", nxt) or re.match(r"^\s*\d+\.\s+", nxt):
                break
            para.append(nxt.strip())
            i += 1
        body.append("<p>" + smart_join([inline(p) for p in para]) + "</p>")
    return "\n".join(body), toc


def build_html(md: str) -> str:
    content, toc = convert(md)
    toc_html = "".join(
        f'<li><a href="#{anchor}"><span class="num">{idx:02d}</span>{inline(re.sub(r"^\d+\.\s*", "", title))}</a></li>'
        for idx, (title, anchor) in enumerate(toc, start=1)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 数字工厂项目报告书</title>
<style>{CSS}</style>
</head>
<body>
<section class="cover">
  <div class="cover-inner">
    <div class="band"></div>
    <h1>AI 数字工厂<br>项目报告书</h1>
    <div class="sub">OIP 三维场景 × Python 离散事件仿真平台</div>
    <div class="meta">
      报告版本：1.1.1<br>
      生成日期：2026-11-03<br>
      交付形态：源码运行 + Windows 离线安装包<br>
      仿真权威：integration/scene/engine.py<br>
      测试结论：79 passed；浏览器五页 0 渲染异常、0 控制台错误
    </div>
    <div class="note">
      声明：本报告中的产能、OEE、能耗、边缘时延等数据均来自软件仿真，
      不是真实工厂或 6G 网络实测。
    </div>
  </div>
</section>
<div class="page-break"></div>
<section class="toc">
  <h2>目录</h2>
  <ol>{toc_html}</ol>
</section>
<main>
{content}
</main>
</body>
</html>
"""


def find_browser() -> str | None:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def build_pdf() -> bool:
    browser = find_browser()
    if not browser:
        print("未找到 Edge/Chrome，跳过 PDF。可打开 HTML 后手动打印。")
        return False
    for old in (PDF_PATH,):
        old.unlink(missing_ok=True)
    command = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={PDF_PATH}",
        HTML_PATH.as_uri(),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0 or not PDF_PATH.exists():
        print("PDF 生成失败：", (result.stderr or result.stdout)[-500:])
        return False
    print(f"PDF 已生成：{PDF_PATH}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true", help="同时生成 PDF")
    args = parser.parse_args()
    if not MD_PATH.exists():
        print(f"找不到报告源文件：{MD_PATH}")
        return 1
    md = MD_PATH.read_text(encoding="utf-8")
    HTML_PATH.write_text(build_html(md), encoding="utf-8")
    print(f"HTML 已生成：{HTML_PATH}")
    if args.pdf:
        build_pdf()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
