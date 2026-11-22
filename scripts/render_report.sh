#!/bin/sh
# report/report.html と report/report_ja.html から PDF を作る（Edge のヘッドレス印刷）
cd "$(dirname "$0")/../report" || exit 1
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
for name in report report_ja; do
  [ -f "$name.html" ] || continue
  "$EDGE" --headless --disable-gpu --no-pdf-header-footer --print-to-pdf="$(cygpath -w "$PWD/$name.pdf")" "file:///$(cygpath -m "$PWD/$name.html")"
done
