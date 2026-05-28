$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = $null
if (Test-Path 'C:\Users\PC\AppData\Local\Programs\Python\Python313\python.exe') {
    $python = 'C:\Users\PC\AppData\Local\Programs\Python\Python313\python.exe'
}
if (-not $python) {
    $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    $python = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    exit 1
}
if ($python -like '*py.exe') {
    & $python -3 (Join-Path $root 'main.py')
} else {
    & $python (Join-Path $root 'main.py')
}
