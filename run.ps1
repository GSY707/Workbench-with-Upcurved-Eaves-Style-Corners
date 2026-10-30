param(
    [string]$Config = (Join-Path $PSScriptRoot 'configs/default.json'),
    [string]$Output = (Join-Path $PSScriptRoot 'outputs/default'),
    [ValidateSet('preview', 'exact')]
    [string]$Mode = 'exact'
)
$ErrorActionPreference = 'Stop'
$generatorPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $generatorPython)) {
    $generatorPythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $generatorPythonCommand) {
        throw '找不到 Python。请用已安装 CadQuery、NumPy、SciPy 的 Python 直接运行 generate.py。'
    }
    $generatorPython = $generatorPythonCommand.Source
}
& $generatorPython (Join-Path $PSScriptRoot 'generate.py') --config $Config --output $Output --mode $Mode
if ($LASTEXITCODE -ne 0) {
    throw "生成失败，Python 退出码为 $LASTEXITCODE。请查看上方具体错误。"
}
