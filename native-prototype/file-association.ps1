param(
    [ValidateSet('Inspect', 'Install', 'Rebind', 'Uninstall')]
    [string]$Action = 'Inspect',
    [string]$Executable = (Join-Path $PSScriptRoot 'bin\win-x64\KPL规格编辑器.exe'),
    [string]$ExpectedExecutable = '',
    [switch]$ConfirmRebind
)

$ErrorActionPreference = 'Stop'
$ext = 'Software\Classes\.kpldoc'
$prog = 'Software\Classes\KPL.SpecDocument.Prototype.1'
$fileExt = 'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.kpldoc'
$progId = 'KPL.SpecDocument.Prototype.1'
$owner = 'KPL-native-prototype-v1'
$exe = [System.IO.Path]::GetFullPath($Executable)
$command = '"' + $exe + '" "%1"'
$cu = [Microsoft.Win32.Registry]::CurrentUser
$lm = [Microsoft.Win32.Registry]::LocalMachine

function Open-Key($base, [string]$path) { return $base.OpenSubKey($path) }
function Read-Value($base, [string]$path, [string]$name) {
    $key = Open-Key $base $path
    if ($null -eq $key) { return $null }
    try { return $key.GetValue($name, $null) } finally { $key.Dispose() }
}
function Exists($base, [string]$path) {
    $key = Open-Key $base $path
    if ($null -eq $key) { return $false }
    $key.Dispose(); return $true
}
function Is-ExactKey([string]$path, [string[]]$valueNames, [string[]]$subNames) {
    $key = Open-Key $cu $path
    if ($null -eq $key) { return $false }
    try {
        $actualValues = $key.GetValueNames(); $actualSubs = $key.GetSubKeyNames()
        return ($actualValues.Length -eq $valueNames.Length) -and ($actualSubs.Length -eq $subNames.Length) -and
               (($actualValues | Sort-Object) -join '|' -ceq (($valueNames | Sort-Object) -join '|')) -and
               (($actualSubs | Sort-Object) -join '|' -ceq (($subNames | Sort-Object) -join '|'))
    } finally { $key.Dispose() }
}
function Is-PristineAssociation([string]$expectedCommand = $command) {
    return (Is-ExactKey $ext @('') @()) -and
        (Is-ExactKey $prog @('', 'KplPrototypeOwner') @('shell')) -and
        (Is-ExactKey ($prog + '\shell') @() @('open')) -and
        (Is-ExactKey ($prog + '\shell\open') @() @('command')) -and
        (Is-ExactKey ($prog + '\shell\open\command') @('') @()) -and
        ((Read-Value $cu $ext '') -ceq $progId) -and
        ((Read-Value $cu $prog '') -ceq 'KPL 规格文档（原型）') -and
        ((Read-Value $cu $prog 'KplPrototypeOwner') -ceq $owner) -and
        ((Read-Value $cu ($prog + '\shell\open\command') '') -ceq $expectedCommand)
}
function Notify-Shell {
    if (-not ('KplAssociationNotify' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class KplAssociationNotify { [DllImport("shell32.dll")] public static extern void SHChangeNotify(uint e, uint f, IntPtr a, IntPtr b); }'
    }
    [KplAssociationNotify]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
}

if ($Action -eq 'Inspect') {
    [pscustomobject]@{
        ExtensionPerUser = Exists $cu $ext
        ExtensionMachine = Exists $lm $ext
        ExplorerRecord = Exists $cu $fileExt
        PrototypePerUser = Exists $cu $prog
        PrototypeMachine = Exists $lm $prog
        CurrentCommand = Read-Value $cu ($prog + '\shell\open\command') ''
        Executable = $exe
    } | Format-List
    return
}
if ($Action -eq 'Install') {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw '找不到原型程序；未修改关联。' }
    if (Is-PristineAssociation) { Write-Output '该原型关联已存在，未重复修改。'; return }
    if ((Exists $cu $ext) -or (Exists $lm $ext) -or (Exists $cu $fileExt) -or (Exists $cu $prog) -or (Exists $lm $prog)) {
        throw '扩展名或程序标识已有记录；为保护现有关联，未修改。请用 Windows“打开方式”处理。'
    }
    try {
        $root = $cu.CreateSubKey($prog)
        try { $root.SetValue('', 'KPL 规格文档（原型）'); $root.SetValue('KplPrototypeOwner', $owner) } finally { $root.Dispose() }
        $verb = $cu.CreateSubKey($prog + '\shell\open\command')
        try { $verb.SetValue('', $command) } finally { $verb.Dispose() }
        if ((Exists $cu $ext) -or (Exists $lm $ext) -or (Exists $cu $fileExt)) { throw '创建过程中出现新的扩展名记录。' }
        $extension = $cu.CreateSubKey($ext)
        try { $extension.SetValue('', $progId) } finally { $extension.Dispose() }
        if (-not (Is-PristineAssociation)) { throw '关联写入后核对失败。' }
        Notify-Shell
        Write-Output '已为当前用户注册 .kpldoc 原型关联；没有修改 .html。'
    } catch {
        if (Is-PristineAssociation) { $cu.DeleteSubKey($ext, $false); $cu.DeleteSubKeyTree($prog, $false) }
        throw ('关联未确认完成；请运行 -Action Inspect 检查是否留下部分记录。' + $_.Exception.Message)
    }
    return
}
if ($Action -eq 'Rebind') {
    if (-not $ConfirmRebind -or -not $ExpectedExecutable) { throw '必须明确指定原路径并确认重绑定；未修改。' }
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw '找不到新编辑器；未修改关联。' }
    if (Exists $cu ($fileExt + '\UserChoice')) { throw 'Windows 已记录受保护的用户选择；未修改关联。' }
    $previousExe = [System.IO.Path]::GetFullPath($ExpectedExecutable)
    $previousCommand = '"' + $previousExe + '" "%1"'
    if (-not (Is-PristineAssociation $previousCommand)) { throw '关联不再与预期的 KPL 自有记录一致；未修改。' }
    if ($previousCommand -ceq $command) { Write-Output 'KPL 关联已指向该程序；未重复修改。'; return }
    $key = $cu.OpenSubKey(($prog + '\shell\open\command'), $true)
    if ($null -eq $key) { throw '关联命令不可写；未修改。' }
    try {
        if ((Exists $cu ($fileExt + '\UserChoice')) -or
            -not (Is-PristineAssociation $previousCommand) -or
            ($key.GetValue('', $null) -cne $previousCommand)) {
            throw '关联在修改前发生变化；未修改。'
        }
        $key.SetValue('', $command)
    } finally { $key.Dispose() }
    if (-not (Is-PristineAssociation)) { throw '写入后状态不确定；请先检查实际关联，不要盲目重试。' }
    Notify-Shell
    Write-Output '已更新当前用户的 KPL 自有 .kpldoc 打开路径；没有修改 .html。'
    return
}
if (-not (Is-PristineAssociation)) { throw '关联已变化或不属于此原型；未删除。' }
if (Exists $cu ($fileExt + '\UserChoice')) {
    throw 'Windows 已记录用户选择；请先在系统“默认应用/打开方式”中调整，再运行卸载。未修改该受保护记录。'
}
$cu.DeleteSubKey($ext, $false)
$cu.DeleteSubKeyTree($prog, $false)
Notify-Shell
Write-Output '已移除本原型创建的当前用户关联；没有修改 .html。'
