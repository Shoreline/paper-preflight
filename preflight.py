#!/usr/bin/env python3
"""投稿前格式检查：拿会议官方模板当基准，核对要上传的论文 PDF 有没有偏离模板。

样式文件一字不改，成品照样可能不合模板：比如换用 XeTeX 编译、手动指定 Times New Roman，
这个字体没有 small caps，模板里的标题和节标题会全退成普通大小写——只在 log 里留一行 warning。
所以这个脚本不读源码下结论，而是看成品：用模板假定的编译器（pdflatex）重编一遍、
扫 log、查上传 PDF 是谁编出来的、数页数，最后把我们的第 1–2 页和官方样例并排出图给人看。

用法：
    python3 ~/code/paper-preflight/preflight.py <论文目录>/main.tex --venue iclr2027

    --venue    templates/ 下的会议目录名；目录里放官方模板原件 + venue.json（页数上限等）
    --template 不用 --venue 时直接给模板目录（此时 --page-limit 必填）
    --pdf      真正要上传的 PDF（默认取 .tex 同名 .pdf）
    --out      报告与截图输出目录（默认 <论文目录>/.preflight/）

结果分三档：FAIL（必须修）/ WARN（人看一眼再决定）/ OK。有 FAIL 时退出码为 1。
报告写在 <out>/report.md，并排截图在 <out>/compare.html（浏览器打开）。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEX_BIN_CANDIDATES = [
    Path.home() / "Library/TinyTeX/bin/universal-darwin",
    Path("/Library/TeX/texbin"),
]

# 改字体 / 版心 / 行距：官方说明 "Tweaking the style files may be grounds for rejection"
FAIL_PREAMBLE = [
    (r"\\usepackage(\[[^\]]*\])?\{fontspec\}", "加载 fontspec（换了模板的字体机制）"),
    (r"\\set(main|sans|mono)font", "手动指定正文字体"),
    (r"\\usepackage(\[[^\]]*\])?\{geometry\}", "geometry 改版心"),
    (r"\\(textheight|textwidth|oddsidemargin|evensidemargin|topmargin|headsep|headheight|footskip)\b",
     "改版心尺寸"),
    (r"\\linespread|\\baselinestretch", "改行距"),
    (r"\\usepackage(\[[^\]]*\])?\{(savetrees|titlesec)\}", "压版面 / 改标题样式的宏包"),
    (r"\\renewcommand\{?\\(normalsize|small|large|section|subsection)", "重定义字号或标题命令"),
]
WARN_PREAMBLE = [
    (r"\\(setlength|addtolength)\s*\{?\\(textfloatsep|intextsep|floatsep|dbltextfloatsep|abovedisplayskip|"
     r"belowdisplayskip|abovedisplayshortskip|belowdisplayshortskip|abovecaptionskip|belowcaptionskip|"
     r"parskip|parindent)",
     "压了浮动体 / 公式 / 段落间距"),
    (r"\\suppressfloats", "压浮动体位置"),
]


def find_tool(name):
    for d in TEX_BIN_CANDIDATES:
        if (d / name).exists():
            return str(d / name)
    return shutil.which(name)


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")


def strip_comments(tex):
    return "\n".join(re.sub(r"(?<!\\)%.*", "", line) for line in tex.splitlines())


def preamble_of(tex_path):
    tex = strip_comments(Path(tex_path).read_text(errors="replace"))
    return tex.split("\\begin{document}", 1)[0], tex


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, check, msg):
        self.rows.append((level, check, msg))
        print(f"[{level:4}] {check}: {msg}")

    @property
    def failed(self):
        return any(r[0] == "FAIL" for r in self.rows)


def compile_pdflatex(src_dir, tex_name, build_dir, pdflatex, bibtex):
    if build_dir.exists():
        shutil.rmtree(build_dir)
    shutil.copytree(src_dir, build_dir, ignore=shutil.ignore_patterns(
        ".preflight", "archive*", "*.pdf.bak", ".git"))
    stem = Path(tex_name).stem
    for f in build_dir.glob(stem + ".*"):
        if f.suffix in (".aux", ".bbl", ".blg", ".log", ".out", ".pdf"):
            f.unlink()
    cmd = [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tex_name]
    run(cmd, cwd=build_dir)
    if bibtex and (build_dir / (stem + ".aux")).exists():
        run([bibtex, stem], cwd=build_dir)
    run(cmd, cwd=build_dir)
    last = run(cmd, cwd=build_dir)
    pdf = build_dir / (stem + ".pdf")
    log = build_dir / (stem + ".log")
    return (pdf if pdf.exists() else None), (log.read_text(errors="replace") if log.exists() else last.stdout)


def pdf_info(pdf):
    out = run(["pdfinfo", str(pdf)]).stdout
    return dict(re.findall(r"^([^:]+):\s*(.*)$", out, re.M))


def pdf_fonts(pdf):
    out = run(["pdffonts", str(pdf)]).stdout.splitlines()[2:]
    fonts = []
    for line in out:
        parts = line.split()
        if len(parts) < 5:
            continue
        name = re.sub(r"^[A-Z]{6}\+", "", parts[0])
        emb = parts[-5]  # 列：name type encoding emb sub uni object ID
        fonts.append((name, emb))
    return fonts


def page_texts(pdf):
    out = run(["pdftotext", "-layout", str(pdf), "-"]).stdout
    return out.split("\f")


def body_end_page(pages, marker_re):
    """返回正文最后一页的页码（1 起）：第一个出现参考文献 / 声明标题的页之前那一页，
    除非标题前面同页还有大段正文。"""
    # small caps 抽成文本会变成「R EFERENCES」，行首还可能带行号：去掉行号与所有空白再比
    pat = re.compile(rf"^({marker_re.replace(' ', '')})$", re.I)
    for i, text in enumerate(pages, 1):
        lines = [l for l in text.splitlines() if l.strip() and not re.fullmatch(r"\s*\d{1,4}\s*", l)]
        for j, line in enumerate(lines):
            if pat.match(re.sub(r"\s+", "", re.sub(r"^\s*\d{3,4}\s+", "", line))):
                # lines[0] 是页眉；标题若在前 2 行内，说明正文在上一页就结束了
                return (i - 1 if j <= 2 else i), i, line.strip()
    return None, None, None


def render(pdf, prefix, pages=(1, 2), dpi=70):
    paths = []
    for p in pages:
        run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", "-f", str(p), "-l", str(p), str(pdf), f"{prefix}-p{p}"])
        png = Path(f"{prefix}-p{p}.png")
        if png.exists():
            paths.append(png)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tex", help="论文主文件，如 paper/iclr2027/main.tex")
    ap.add_argument("--venue", help="templates/ 下的会议目录名，如 iclr2027")
    ap.add_argument("--template", help="官方模板原件目录（含 .sty 与样例 .tex）；给了 --venue 可省")
    ap.add_argument("--page-limit", type=int, help="正文页数上限（不含参考文献与声明）；默认读 venue.json")
    ap.add_argument("--pdf", help="要上传的 PDF（默认 .tex 同名 .pdf）")
    ap.add_argument("--out", help="输出目录（默认 <论文目录>/.submission_check）")
    ap.add_argument("--body-end", default=None,
                    help="正文之后第一个标题的正则（用来数正文页）")
    ap.add_argument("--anon-string", default=None,
                    help="匿名版第 1 页必须出现的字样；传空串跳过")
    args = ap.parse_args()

    tex = Path(args.tex).resolve()
    paper_dir = tex.parent
    if args.venue:
        template = Path(__file__).resolve().parent / "templates" / args.venue
    elif args.template:
        template = Path(args.template).resolve()
    else:
        sys.exit("要给 --venue 或 --template")
    if not template.is_dir():
        sys.exit(f"没有模板目录 {template}")
    venue_cfg = template / "venue.json"
    cfg = json.loads(venue_cfg.read_text()) if venue_cfg.exists() else {}
    for key, default in (("page_limit", None), ("anon_string", "Anonymous authors"),
                         ("body_end", r"REFERENCES|REPRODUCIBILITY STATEMENT|ETHICS STATEMENT|"
                                      r"AI USE STATEMENT|ACKNOWLEDG(E)?MENTS?|LLM USAGE")):
        if getattr(args, key) is None:
            setattr(args, key, cfg.get(key, default))
    if args.page_limit is None:
        sys.exit("没有页数上限：传 --page-limit 或在模板目录的 venue.json 里写 page_limit")
    upload_pdf = Path(args.pdf).resolve() if args.pdf else tex.with_suffix(".pdf")
    out = Path(args.out).resolve() if args.out else paper_dir / ".preflight"
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()

    pdflatex, bibtex = find_tool("pdflatex"), find_tool("bibtex")
    for t in ("pdfinfo", "pdffonts", "pdftotext", "pdftoppm"):
        if not shutil.which(t):
            sys.exit(f"缺 {t}（brew install poppler）")
    if not pdflatex:
        rep.add("FAIL", "编译器", "找不到 pdflatex。装 TinyTeX：curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh")

    # 1. 样式文件与官方逐字节比对
    style_files = [f for f in template.iterdir() if f.suffix in (".sty", ".bst", ".cls")]
    for f in sorted(style_files):
        mine = paper_dir / f.name
        if not mine.exists():
            rep.add("WARN", "样式文件", f"论文目录里没有 {f.name}（会用系统里的版本）")
        elif mine.read_bytes() != f.read_bytes():
            rep.add("FAIL", "样式文件", f"{f.name} 与官方原件不同（diff {mine} {f}）")
    if not any(r[1] == "样式文件" for r in rep.rows):
        rep.add("OK", "样式文件", f"{len(style_files)} 个与官方逐字节相同")

    # 2. 开头命令扫描
    pre, full = preamble_of(tex)
    hits = 0
    seen = set()
    for level, rules in (("FAIL", FAIL_PREAMBLE), ("WARN", WARN_PREAMBLE)):
        for pat, why in rules:
            for m in re.finditer(pat, pre):
                lineno = pre[:m.start()].count("\n") + 1
                if (lineno, why) in seen:
                    continue
                seen.add((lineno, why))
                rep.add(level, "开头命令", f"{tex.name}:{lineno} {why}：{pre.splitlines()[lineno - 1].strip()[:120]}")
                hits += 1
    if re.search(r"^\s*\\\w*finalcopy\b", pre, re.M):
        rep.add("FAIL", "匿名", "开头启用了 final copy 命令（会印出作者）")
        hits += 1
    neg_vspace = len(re.findall(r"\\vspace\*?\{\s*-", full))
    if neg_vspace:
        rep.add("WARN", "开头命令", f"全文有 {neg_vspace} 处负 \\vspace（压版面）")
    if not hits:
        rep.add("OK", "开头命令", "没有改字体 / 版心 / 行距 / 间距的命令")
    tpl_tex = next((f for f in sorted(template.glob("*.tex"))
                    if "\\documentclass" in strip_comments(f.read_text(errors="replace"))), None)
    if tpl_tex:
        def pkgs(text):
            return {p.strip() for g in re.findall(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", text)
                    for p in g.split(",")}
        tpl_pkgs, my_pkgs = pkgs(preamble_of(tpl_tex)[0]), pkgs(pre)
        extra = sorted(my_pkgs - tpl_pkgs)
        rep.add("OK", "宏包", f"比官方样例多加载：{', '.join(extra) or '无'}")

    # 3. 用 pdflatex 重编
    build_pdf = None
    if pdflatex:
        build_pdf, log = compile_pdflatex(paper_dir, tex.name, out / "build", pdflatex, bibtex)
        if not build_pdf:
            rep.add("FAIL", "pdflatex 编译", f"编不出 PDF，看 {out / 'build' / (tex.stem + '.log')}")
        else:
            rep.add("OK", "pdflatex 编译", f"{build_pdf}")
            # 4. 扫 log
            for pat, level, why in [
                (r"Font shape `[^']+' undefined", "FAIL", "字体缺字形（如 small caps 退化）"),
                (r"Some font shapes were not available", "FAIL", "有字形被默认字体替代"),
                (r"Citation `[^']+' (on page \d+ )?undefined", "WARN", "引用未定义"),
                (r"Reference `[^']+' on page \d+ undefined", "WARN", "交叉引用未定义"),
            ]:
                ms = [m.group(0) for m in re.finditer(pat, log)]
                if ms:
                    rep.add(level, "编译 log", f"{why}：{len(ms)} 处，例：{ms[0]}")
            over = [float(x) for x in re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)", log)]
            big = [x for x in over if x > 5]
            if big:
                rep.add("WARN", "编译 log", f"{len(big)} 处行宽超出 5pt 以上（最大 {max(big):.1f}pt），可能伸进页边")

    # 5. 上传的 PDF：谁编的、字体、页数
    check_pdf = upload_pdf if upload_pdf.exists() else build_pdf
    if upload_pdf.exists():
        info = pdf_info(upload_pdf)
        prod = f"{info.get('Creator', '')} / {info.get('Producer', '')}"
        md5 = hashlib.md5(upload_pdf.read_bytes()).hexdigest()
        rep.add("OK", "上传 PDF", f"{upload_pdf}（md5 {md5}，{info.get('Pages')} 页）")
        if "pdfTeX" not in prod:
            rep.add("FAIL", "上传 PDF", f"不是 pdflatex 编出来的（{prod}）；模板按 pdflatex 设计，换编译器会悄悄改字体")
        not_emb = [n for n, e in pdf_fonts(upload_pdf) if e != "yes"]
        if not_emb:
            rep.add("FAIL", "上传 PDF", f"字体未嵌入：{', '.join(sorted(set(not_emb)))}")
        if build_pdf:
            up_fonts = {n for n, _ in pdf_fonts(upload_pdf)}
            ref_fonts = {n for n, _ in pdf_fonts(build_pdf)}
            odd = sorted(up_fonts - ref_fonts)
            if odd:
                rep.add("WARN", "上传 PDF", f"用了 pdflatex 重编版里没有的字体：{', '.join(odd[:8])}")
            bp = pdf_info(build_pdf).get("Pages")
            if bp != info.get("Pages"):
                rep.add("WARN", "上传 PDF", f"页数与 pdflatex 重编版不同（{info.get('Pages')} vs {bp}），上传的可能不是最新源码编的")
    else:
        rep.add("WARN", "上传 PDF", f"没找到 {upload_pdf}，以下只查 pdflatex 重编版")

    # 6. 页数、页眉、匿名。上传版与 pdflatex 重编版都数：换编译器会改变分页
    to_count = [("上传版", upload_pdf if upload_pdf.exists() else None), ("pdflatex 重编版", build_pdf)]
    for label, pdf in to_count:
        if not pdf:
            continue
        pages = page_texts(pdf)
        end, marker_page, marker = body_end_page(pages, args.body_end)
        if end is None:
            rep.add("WARN", "正文页数", f"{label}：没找到参考文献 / 声明标题，正文页数没法数，人工看")
        elif end > args.page_limit:
            rep.add("FAIL", "正文页数", f"{label}：正文到第 {end} 页，超上限 {args.page_limit}（第 {marker_page} 页出现「{marker}」）")
        else:
            rep.add("OK", "正文页数", f"{label}：正文到第 {end} 页（上限 {args.page_limit}；第 {marker_page} 页起是「{marker}」）")
        if args.anon_string and args.anon_string.lower() not in pages[0].lower():
            rep.add("FAIL", "匿名", f"{label}：第 1 页没有「{args.anon_string}」")

    # 7. 官方样例同样编一遍，并排出图
    official_pdf = None
    if pdflatex and tpl_tex:
        official_pdf, _ = compile_pdflatex(template, tpl_tex.name, out / "official", pdflatex, bibtex)
    if check_pdf and official_pdf:
        head_mine = next((l.strip() for l in page_texts(check_pdf)[0].splitlines() if l.strip()), "")
        head_tpl = next((l.strip() for l in page_texts(official_pdf)[0].splitlines() if l.strip()), "")
        if head_mine != head_tpl:
            rep.add("FAIL", "页眉", f"「{head_mine}」≠ 官方「{head_tpl}」")
        else:
            rep.add("OK", "页眉", head_mine)
        mine_png = render(check_pdf, out / "mine")
        tpl_png = render(official_pdf, out / "official")
        cells = "".join(
            f"<tr><td><img src='{a.name}'></td><td><img src='{b.name}'></td></tr>" for a, b in zip(mine_png, tpl_png))
        (out / "compare.html").write_text(
            "<!doctype html><meta charset=utf-8><title>模板并排对照</title>"
            "<style>body{font-family:sans-serif;margin:16px}img{border:1px solid #999;max-width:100%}"
            "td{vertical-align:top;padding:4px}</style>"
            f"<h2>左：要上传的 PDF　右：官方样例（同一 pdflatex）</h2>"
            "<p>逐项对：标题与节标题是否小型大写（全大写、首字母略大）、ABSTRACT 居中、左侧行号、页眉年份、"
            "作者处为匿名、正文字体与字号、页边距。</p>"
            f"<table>{cells}</table>")
        rep.add("WARN", "目视对照", f"必须人看一眼：open {out / 'compare.html'}")

    # 报告
    lines = ["# 投稿前格式检查", "", f"- 论文：`{tex}`", f"- 模板：`{template}`", "",
             "| 结果 | 检查项 | 说明 |", "|---|---|---|"]
    order = {"FAIL": 0, "WARN": 1, "OK": 2}
    for level, check, msg in sorted(rep.rows, key=lambda r: order[r[0]]):
        lines.append(f"| {level} | {check} | {msg.replace('|', '/')} |")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    n_fail = sum(r[0] == "FAIL" for r in rep.rows)
    print(f"\n{'不能提交' if n_fail else '可以提交（WARN 逐条看过后）'}：{n_fail} 项 FAIL。报告 {out / 'report.md'}")
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()
