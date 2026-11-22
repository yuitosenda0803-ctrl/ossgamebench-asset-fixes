# 00_fetch_dataset.ps1
# OSSGameBench (Zenodo record 17553539: Zenodo 上の 3 版のうち 2 版目で、データ本体を含む唯一の版) の 65.8GB の zip から、
# 分析に必要な parquet だけを HTTP Range リクエストで部分取得して展開する。
# (コミット本体 973 ファイル・圧縮 65.28GB はこのスクリプトでは取得しない。必要なものは 05_fetch_commits.py で取得する)
#
# 使い方:  powershell -ExecutionPolicy Bypass -File scripts\00_fetch_dataset.ps1 [-OutDir <出力先>]
# 必要なもの: Windows 10/11 標準の curl.exe (7.66 以降, --parallel 対応)

param(
    [string]$OutDir = (Join-Path $PSScriptRoot "..\分析対象データ"),
    [string]$WorkDir = "C:\data\OSSGameBench\_work",
    [int]$ChunkMB = 8,
    [int]$Parallel = 8
)
$ErrorActionPreference = "Stop"

$Url       = "https://zenodo.org/api/records/17553539/files/OSSGameBench.zip/content"
$ZipSize   = [uint64]65816061871
$Targets   = @(
    "OSSGameBench/project.parquet",
    "OSSGameBench/issue.parquet",
    "OSSGameBench/issue_comment.parquet",
    "OSSGameBench/pr.parquet",
    "OSSGameBench/pr_comment.parquet",
    "OSSGameBench/issue_pr.parquet",
    "OSSGameBench/pr_commit.parquet",
    "OSSGameBench/commit_metadata.parquet"
)

New-Item -ItemType Directory -Force $OutDir, $WorkDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

function Get-Range([uint64]$from, [uint64]$to, [string]$path) {
    & curl.exe -sS -L --retry 10 --retry-all-errors -r "$from-$to" -o $path $Url
    if ($LASTEXITCODE -ne 0) { throw "curl failed ($LASTEXITCODE) for range $from-$to" }
}
function U16($b, $i) { [BitConverter]::ToUInt16($b, $i) }
function U32($b, $i) { [BitConverter]::ToUInt32($b, $i) }
function U64($b, $i) { [BitConverter]::ToUInt64($b, $i) }

# --- 1. 末尾から ZIP64 End of Central Directory を読み、セントラルディレクトリを取得 ---
$tail = Join-Path $WorkDir "tail.bin"
Get-Range ($ZipSize - 65536) ($ZipSize - 1) $tail
$b = [IO.File]::ReadAllBytes($tail); $tailBase = $ZipSize - 65536
$loc = $null
for ($i = $b.Length - 20; $i -ge 0; $i--) { if ((U32 $b $i) -eq 0x07064b50) { $loc = $i; break } }
if ($null -eq $loc) { throw "ZIP64 locator not found" }
$eocd64 = [int]((U64 $b ($loc + 8)) - $tailBase)
$cdSize = U64 $b ($eocd64 + 40); $cdOff = U64 $b ($eocd64 + 48)

$cdPath = Join-Path $WorkDir "central_dir.bin"
Get-Range $cdOff ($cdOff + $cdSize - 1) $cdPath
$b = [IO.File]::ReadAllBytes($cdPath)

$entries = @{}; $p = 0
while ($p -lt $b.Length -and (U32 $b $p) -eq 0x02014b50) {
    $meth = U16 $b ($p + 10); $crc = U32 $b ($p + 16)
    $csz = [uint64](U32 $b ($p + 20)); $usz = [uint64](U32 $b ($p + 24))
    $nl = U16 $b ($p + 28); $xl = U16 $b ($p + 30); $cl = U16 $b ($p + 32)
    $off = [uint64](U32 $b ($p + 42))
    $name = [Text.Encoding]::UTF8.GetString($b, $p + 46, $nl)
    $x = $p + 46 + $nl; $xe = $x + $xl
    while ($x -lt $xe) {  # ZIP64 extra field (id=1)
        $id = U16 $b $x; $sz = U16 $b ($x + 2)
        if ($id -eq 1) {
            $q = $x + 4
            if ($usz -eq 0xFFFFFFFF) { $usz = U64 $b $q; $q += 8 }
            if ($csz -eq 0xFFFFFFFF) { $csz = U64 $b $q; $q += 8 }
            if ($off -eq 0xFFFFFFFF) { $off = U64 $b $q; $q += 8 }
        }
        $x += 4 + $sz
    }
    $entries[$name] = [pscustomobject]@{ method = $meth; crc = $crc; csize = $csz; usize = $usz; offset = $off }
    $p = $xe + $cl
}
Write-Host "central directory: $($entries.Count) entries"

# --- 2. 各対象ファイルについて圧縮データ部分を並列 Range 取得し、Deflate 展開 ---
foreach ($name in $Targets) {
    $e = $entries[$name]; if (-not $e) { throw "not in zip: $name" }
    $fileName = Split-Path $name -Leaf
    $dest = Join-Path $OutDir $fileName
    if ((Test-Path $dest) -and (Get-Item $dest).Length -eq $e.usize) { Write-Host "skip (exists): $fileName"; continue }

    # ローカルヘッダ (30byte + name + extra) を読み、データ開始位置を求める
    $lh = Join-Path $WorkDir "lh.bin"
    Get-Range $e.offset ($e.offset + 29) $lh
    $h = [IO.File]::ReadAllBytes($lh)
    if ((U32 $h 0) -ne 0x04034b50) { throw "bad local header: $name" }
    $dataStart = $e.offset + 30 + (U16 $h 26) + (U16 $h 28)
    $dataEnd = $dataStart + $e.csize - 1

    $partDir = Join-Path $WorkDir ($fileName + ".parts"); New-Item -ItemType Directory -Force $partDir | Out-Null
    $chunk = [uint64]($ChunkMB * 1MB); $parts = @(); $curlArgs = @('--parallel', '--parallel-max', "$Parallel", '-sS', '-L', '--retry', '10', '--retry-all-errors')
    for ($s = $dataStart; $s -le $dataEnd; $s += $chunk) {
        $t = [Math]::Min($s + $chunk - 1, $dataEnd)
        $pf = Join-Path $partDir ("{0:D5}" -f $parts.Count); $parts += [pscustomobject]@{ path = $pf; len = $t - $s + 1; from = $s; to = $t }
        if (-not ((Test-Path $pf) -and (Get-Item $pf).Length -eq ($t - $s + 1))) { $curlArgs += @('-r', "$s-$t", '-o', $pf, $Url) }
    }
    Write-Host ("downloading {0} ({1:N1} MB, {2} chunks)..." -f $fileName, ($e.csize / 1MB), $parts.Count)
    if ($curlArgs.Count -gt 9) { & curl.exe @curlArgs }
    foreach ($pt in $parts) {  # 不完全なチャンクを再取得
        if (-not ((Test-Path $pt.path) -and (Get-Item $pt.path).Length -eq $pt.len)) { Get-Range $pt.from $pt.to $pt.path }
    }

    # 結合して展開
    $deflated = Join-Path $WorkDir ($fileName + ".deflate")
    $fs = [IO.File]::Create($deflated)
    foreach ($pt in $parts) { $bytes = [IO.File]::ReadAllBytes($pt.path); $fs.Write($bytes, 0, $bytes.Length) }
    $fs.Close()
    if ((Get-Item $deflated).Length -ne $e.csize) { throw "size mismatch (compressed): $name" }

    $tmp = "$dest.tmp"
    if ($e.method -eq 8) {
        $in = [IO.File]::OpenRead($deflated); $ds = New-Object IO.Compression.DeflateStream($in, [IO.Compression.CompressionMode]::Decompress)
        $out = [IO.File]::Create($tmp); $ds.CopyTo($out); $out.Close(); $ds.Close()
    } elseif ($e.method -eq 0) { Copy-Item $deflated $tmp } else { throw "unsupported method $($e.method)" }

    # 検証: 展開後サイズ と parquet マジックナンバー (先頭・末尾 "PAR1")
    $len = (Get-Item $tmp).Length
    $fsr = [IO.File]::OpenRead($tmp); $m = New-Object byte[] 4
    [void]$fsr.Read($m, 0, 4); $head = [Text.Encoding]::ASCII.GetString($m)
    [void]$fsr.Seek(-4, 'End'); [void]$fsr.Read($m, 0, 4); $foot = [Text.Encoding]::ASCII.GetString($m); $fsr.Close()
    if ($len -ne $e.usize -or $head -ne 'PAR1' -or $foot -ne 'PAR1') { throw "verification failed: $name (size $len/$($e.usize), $head/$foot)" }
    Move-Item -Force $tmp $dest
    Remove-Item -Recurse -Force $partDir, $deflated
    Write-Host ("  OK -> {0} ({1:N1} MB)" -f $dest, ($len / 1MB))
}
Write-Host "done."
