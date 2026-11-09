param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$identityAwaitMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-IdentityOperation($Operation, [Type]$ResultType) {
    $identityTask = $identityAwaitMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $identityTask.Wait()
    return $identityTask.Result
}
$identityFile = Await-IdentityOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$identityStream = Await-IdentityOperation ($identityFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $identityDecoder = Await-IdentityOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($identityStream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $identityBitmap = Await-IdentityOperation ($identityDecoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    try {
        $identityEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('de-DE'))
        if ($null -eq $identityEngine) { $identityEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
        if ($null -eq $identityEngine) { throw 'LOCAL_OCR_LANGUAGE_UNAVAILABLE' }
        $identityResult = Await-IdentityOperation ($identityEngine.RecognizeAsync($identityBitmap)) ([Windows.Media.Ocr.OcrResult])
        $identityLines = @($identityResult.Lines | ForEach-Object { $_.Text })
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        ConvertTo-Json -InputObject $identityLines -Compress
    } finally { $identityBitmap.Dispose() }
} finally { $identityStream.Dispose() }
