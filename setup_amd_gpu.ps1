# ============================================================================
#  setup_amd_gpu.ps1
#  Install ROCm PyTorch + Demucs for AMD GPU (e.g. RX 9070 XT / gfx1201)
#
#  Steps:
#    1. Check / require Python 3.12 (ROCm official wheels are cp312 only)
#    2. Create .venv312 virtual environment in this folder
#    3. Install AMD official ROCm SDK + torch / torchaudio (ROCm 7.2.1)
#    4. Install demucs + pydub, then force ROCm torch back on top
#    5. Verify the GPU is detected
#
#  Run:
#    powershell -ExecutionPolicy Bypass -File .\setup_amd_gpu.ps1
#
#  Notes:
#    - Requires the AMD "PyTorch on Windows" driver (7.2.1 needs Adrenalin 26.2.2+).
#      Driver: https://www.amd.com/en/support
#    - If versions change, edit $RocmBase / wheel file names below.
#      Official page:
#      https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html
#
#  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 without a
#        BOM using the system ANSI codepage, which corrupts non-ASCII text.
# ============================================================================

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$RocmBase = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1"
$VenvDir  = ".venv312"

# --- wheel URL lists -------------------------------------------------------
$RocmSdk = @(
    "$RocmBase/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
    "$RocmBase/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
    "$RocmBase/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl",
    "$RocmBase/rocm-7.2.1.tar.gz"
)
$TorchWheels = @(
    "$RocmBase/torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
    "$RocmBase/torchaudio-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl",
    "$RocmBase/torchvision-0.24.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl"
)

# --- [1/5] Python 3.12 -----------------------------------------------------
Write-Host "==== [1/5] Checking Python 3.12 ====" -ForegroundColor Cyan
py -3.12 --version
if (-not $?) {
    Write-Host "Python 3.12 not found. Install it (winget install Python.Python.3.12), reopen the window, then rerun." -ForegroundColor Yellow
    return
}

# --- [2/5] venv ------------------------------------------------------------
Write-Host "==== [2/5] Creating venv $VenvDir ====" -ForegroundColor Cyan
if (-not (Test-Path $VenvDir)) { py -3.12 -m venv $VenvDir }
$Py = Join-Path $VenvDir "Scripts\python.exe"
& $Py -m pip install --upgrade pip
if (-not $?) { Write-Host "pip upgrade failed" -ForegroundColor Red; return }

# --- [3/5] ROCm SDK + ROCm PyTorch ----------------------------------------
Write-Host "==== [3/5] Installing ROCm SDK + ROCm PyTorch (large download, several minutes) ====" -ForegroundColor Cyan
& $Py -m pip install --no-cache-dir @RocmSdk
if (-not $?) { Write-Host "ROCm SDK install failed" -ForegroundColor Red; return }
& $Py -m pip install --no-cache-dir @TorchWheels
if (-not $?) { Write-Host "ROCm torch install failed" -ForegroundColor Red; return }

# --- [4/5] demucs + pydub --------------------------------------------------
Write-Host "==== [4/5] Installing demucs + pydub ====" -ForegroundColor Cyan
# demucs pulls its own torch; install it first, then force ROCm torch back on top.
& $Py -m pip install demucs pydub tqdm
if (-not $?) { Write-Host "demucs/pydub install failed" -ForegroundColor Red; return }
Write-Host "Re-pinning ROCm torch over any version demucs pulled in..." -ForegroundColor DarkGray
& $Py -m pip install --no-cache-dir --force-reinstall --no-deps @TorchWheels
if (-not $?) { Write-Host "ROCm torch re-pin failed" -ForegroundColor Red; return }

# --- [5/5] verify ----------------------------------------------------------
Write-Host "==== [5/5] Verifying GPU ====" -ForegroundColor Cyan
& $Py audio_tool.py gpu

Write-Host ""
Write-Host "Done. Use the venv python to run, e.g.:" -ForegroundColor Green
Write-Host "  $VenvDir\Scripts\python.exe audio_tool.py vocals input.mp3 -o out\ -d cuda" -ForegroundColor Green
Write-Host "  $VenvDir\Scripts\python.exe audio_tool.py pipeline input.mp3 -o clean.mp3" -ForegroundColor Green
