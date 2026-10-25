# paper-preflight

投稿前跑一遍，核对要上传的论文 PDF 有没有偏离会议官方模板。有 FAIL 不许上传。

## 为什么要有它

会议对「没用官方模板」的论文可以不审直接拒，而样式文件一字不改并不等于成品合格。典型例子：ML 会议模板都按 pdflatex 设计，换用 tectonic / XeLaTeX 编译后 `\usepackage{times}` 不生效，如果为了用上 Times 手动加 `\setmainfont{Times New Roman}`，这个字体没有小型大写字母，模板里的标题和节标题会全部退成普通大小写。页边距、字号、页眉、行号都还是对的，编译 log 里也只有一行 warning，读源码很难看出来。

现有的开源工具查不到这类问题：[aclpubcheck](https://github.com/acl-org/aclpubcheck)（只适配 ACL，查页边距、字体嵌入、纸张）、[banal](https://www.sysnet.ucsd.edu/banal)（HotCRP / EDAS 内置，查页边距与字号）、IEEE PDF eXpress（查字体嵌入与 PDF 版本）都只核尺寸类规则，而上面这类问题里尺寸全是对的。这个工具只补那块空白：**用模板假定的编译器重编一遍，看成品和官方样例长得一样不一样**。投 ACL 系会议时另外再跑 aclpubcheck。

## 用法

```
python3 ~/code/paper-preflight/preflight.py <论文目录>/main.tex --venue iclr2027
```

报告写在 `<论文目录>/.preflight/report.md`，并排截图在 `<论文目录>/.preflight/compare.html`。有 FAIL 时退出码为 1。论文仓库记得把 `.preflight/` 加进 `.gitignore`。

依赖：Python 3 标准库；poppler（`brew install poppler`，提供 `pdfinfo` / `pdffonts` / `pdftotext` / `pdftoppm`）；pdflatex。本机没有 TeX 发行版时装 TinyTeX，装在用户目录、不要 sudo：

```
curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh
~/Library/TinyTeX/bin/universal-darwin/tlmgr install eso-pic multirow wrapfig pgf fancyhdr courier
```

脚本会先找 `~/Library/TinyTeX/bin/universal-darwin/`、`/Library/TeX/texbin/`，再找 PATH。

## 它查什么

| 检查项 | 怎么查 | 判 FAIL 的情况 |
|---|---|---|
| 样式文件 | 论文目录里的 `.sty` / `.bst` / `.cls` 与官方原件逐字节比 | 有任何不同 |
| 开头命令 | 扫 `\begin{document}` 之前 | 换字体（`fontspec`、`\setmainfont`）、改版心（`geometry`、`\textheight` 等）、改行距、改标题命令、开了 final copy；压浮动体 / 公式间距只判 WARN |
| 重编 | 复制一份用 pdflatex + bibtex 编三遍，扫 log | 缺字形（`Font shape ... undefined`）、字形被替代 |
| 上传的 PDF | `pdfinfo` / `pdffonts` | 不是 pdfTeX 编出来的；字体没嵌入 |
| 正文页数 | 找第一个参考文献 / 声明标题，上传版与重编版都数 | 超上限 |
| 页眉、匿名 | 第 1 页文本与官方样例比 | 页眉不同（如年份）；没有匿名字样 |
| 目视对照 | 第 1–2 页和官方样例并排出图 | 永远判 WARN，**必须人看一眼** |

## 加一个会议

在 `templates/` 下建会议目录（如 `neurips2027/`），放：

1. 官方模板原件，一个字不改（`.sty`、`.bst`、样例 `.tex` 等）；
2. `SOURCE.md`：从哪下的、哪天下的；
3. `venue.json`：

```json
{
  "name": "NeurIPS 2027 (review version)",
  "page_limit": 9,
  "anon_string": "Anonymous Author(s)",
  "body_end": "REFERENCES|ACKNOWLEDG(E)?MENTS?|..."
}
```

`body_end` 是正文之后第一个标题的正则，用来数正文页；`anon_string` 是匿名版第 1 页必须出现的字样，填空串则跳过。每个会议的这几项都以当年作者指南为准。

## 已知局限

- 只在 ICLR 2027 模板和一篇论文上实测过。
- 会议模板原件不随仓库分发（ICLR 官方模板仓库没有声明许可证）：`templates/<会议>/` 里只提交 `venue.json` 和 `SOURCE.md`，用前按 `SOURCE.md` 里的命令下载。
- 正文页数靠找标题定位，换了标题写法要改 `body_end`。
- 默认会议模板都按 pdflatex 设计；要求 XeLaTeX / LuaLaTeX 的会议，「不是 pdfTeX 编出来」这条要改。
