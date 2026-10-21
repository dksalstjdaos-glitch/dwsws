# 메모리 사용을 제한하고 로컬 여행 챗봇을 실행합니다.
$ErrorActionPreference = 'Stop'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$runtimeDirectory = Join-Path $PSScriptRoot '.tmp'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
try {
    $healthResponse = Invoke-WebRequest 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing -TimeoutSec 2
    if ($healthResponse.StatusCode -eq 200) {
        Write-Output '여행 챗봇 실행 중: http://127.0.0.1:8501/'
        return
    }
} catch { }
Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') `
    -ArgumentList '-m','streamlit','run','app.py','--server.address','127.0.0.1','--server.port','8501','--server.headless','true','--browser.gatherUsageStats','false' `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtimeDirectory 'streamlit.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDirectory 'streamlit.stderr.log')
Write-Output '여행 챗봇 시작 중: http://127.0.0.1:8501/'
