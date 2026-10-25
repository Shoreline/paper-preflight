官方原件放这个目录，一个字不改。来源 https://github.com/ICLR/Master-Template 的 `iclr2027/`（`.sty` 文件日期 2026-07-28）。

模板原件不随本仓库分发，用前下载：

```
cd templates/iclr2027
curl -sL https://github.com/ICLR/Master-Template/archive/refs/heads/master.zip -o /tmp/iclr-mt.zip
unzip -joq /tmp/iclr-mt.zip 'Master-Template-master/iclr2027/*' -d .
```

`preflight.py --venue iclr2027` 拿这个目录当比对基准：样式文件逐字节比对、用同一编译器编译 `iclr2027_conference.tex` 生成官方样例页做并排截图。
