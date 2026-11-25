$ErrorActionPreference = "Stop"

$frontendRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$projectRoot = (Resolve-Path (Join-Path $frontendRoot "..")).Path
$demoRoot = Join-Path $env:TEMP ("eolica-demo-" + [guid]::NewGuid().ToString("N"))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "No existe .venv\Scripts\python.exe. Crea el entorno e instala backend\requirements.txt."
}

function Test-LocalPortInUse([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$apiPort = 8001
while (Test-LocalPortInUse $apiPort) { $apiPort++ }
$webPort = 5173
while (Test-LocalPortInUse $webPort) { $webPort++ }

New-Item -ItemType Directory -Force -Path `
    (Join-Path $demoRoot "backend"), `
    (Join-Path $demoRoot "database"), `
    (Join-Path $demoRoot "ciencia-datos\artefactos"), `
    (Join-Path $demoRoot "ciencia-datos\datos") | Out-Null

Copy-Item (Join-Path $projectRoot "backend\main.py") (Join-Path $demoRoot "backend\main.py")
Copy-Item (Join-Path $projectRoot "backend\demo_local.py") (Join-Path $demoRoot "backend\demo_local.py")
Copy-Item (Join-Path $projectRoot "database\schema.sql") (Join-Path $demoRoot "database\schema.sql")
Copy-Item (Join-Path $projectRoot "ciencia-datos\artefactos\modelo_turbina.joblib") (Join-Path $demoRoot "ciencia-datos\artefactos\modelo_turbina.joblib")

$inicio = [datetime]::new(2020, 1, 1, 0, 0, 0)
$cultura = [Globalization.CultureInfo]::InvariantCulture
$filasDemo = for ($indice = 0; $indice -lt 100; $indice++) {
    $velocidad = 3.0 + ($indice % 25) * 0.6
    [pscustomobject][ordered]@{
        "Date/Time" = $inicio.AddMinutes($indice).ToString("dd MM yyyy HH:mm", $cultura)
        "Wind Speed (m/s)" = $velocidad
        "Wind Direction ($([char]0x00B0))" = ($indice * 37) % 360
        "LV ActivePower (kW)" = [math]::Round([math]::Max(0, ($velocidad - 3) * 300), 2)
    }
}
$rutaCsvDemo = Join-Path $demoRoot "ciencia-datos\datos\demo_training.csv"
$filasDemo | Export-Csv -Path $rutaCsvDemo -NoTypeInformation -Encoding utf8

$env:PYTHONPATH = $demoRoot
$env:API_DEMO_MODE = "1"
$env:API_PORT = "$apiPort"
Remove-Item Env:API_USERNAME -ErrorAction SilentlyContinue
Remove-Item Env:API_PASSWORD -ErrorAction SilentlyContinue
$apiProcess = Start-Process -FilePath $python `
    -ArgumentList "-m backend.demo_local" `
    -WorkingDirectory $demoRoot `
    -RedirectStandardOutput (Join-Path $demoRoot "api.log") `
    -RedirectStandardError (Join-Path $demoRoot "api-error.log") `
    -PassThru

$env:API_PROXY_TARGET = "http://127.0.0.1:$apiPort"
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$webProcess = Start-Process -FilePath $npm `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$webPort", "--strictPort") `
    -WorkingDirectory $frontendRoot `
    -RedirectStandardOutput (Join-Path $demoRoot "vite.log") `
    -RedirectStandardError (Join-Path $demoRoot "vite-error.log") `
    -PassThru

Write-Output "Demo web: http://127.0.0.1:$webPort/"
Write-Output "API loopback: http://127.0.0.1:$apiPort/"
Write-Output ('Para detener ambos listeners: Get-NetTCPConnection -LocalPort {0},{1} -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {{ Stop-Process -Id $_ }}' -f $apiPort, $webPort)
Write-Output "SQLite y modelo activo aislados en: $demoRoot\database"
Write-Output "CSV sintético para la carga: $rutaCsvDemo"