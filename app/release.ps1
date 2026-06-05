# 앱 릴리스 자동화: 버전 올리고 빌드 → 버전명 APK → GitHub Release → version.json 갱신
# 사용법:  cd app ;  .\release.ps1 1.0.2 "변경 요약"
param(
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$Notes = "업데이트"
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$apkName = "jbax-v$Version.apk"
$apkUrl  = "https://github.com/duelspost-droid/jblunch/releases/download/v$Version/$apkName"

Write-Host "== 1) 버전 반영 ($Version) =="
# index.html APP_VERSION
(Get-Content "$root\index.html" -Raw) -replace "const APP_VERSION = '[^']*';", "const APP_VERSION = '$Version';" |
  Set-Content "$root\index.html" -Encoding utf8 -NoNewline
# build.gradle versionName + versionCode(+1)
$bg = Get-Content "$root\app\android\app\build.gradle" -Raw
$code = [int]([regex]::Match($bg, 'versionCode\s+(\d+)').Groups[1].Value) + 1
$bg = $bg -replace 'versionCode\s+\d+', "versionCode $code"
$bg = $bg -replace 'versionName\s+"[^"]*"', "versionName `"$Version`""
Set-Content "$root\app\android\app\build.gradle" $bg -Encoding ascii -NoNewline
# version.json
@{ version=$Version; apk=$apkUrl; notes=$Notes } | ConvertTo-Json |
  Set-Content "$root\version.json" -Encoding utf8

Write-Host "== 2) 번들 동기화 + 빌드 =="
Copy-Item "$root\index.html" "$root\app\www\index.html" -Force
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"; $env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
Push-Location "$root\app"
npx cap sync android | Out-Null
Pop-Location
Push-Location "$root\app\android"
& .\gradlew.bat assembleDebug --no-daemon
Pop-Location

$built = "$root\app\android\app\build\outputs\apk\debug\app-debug.apk"
if (-not (Test-Path $built)) { throw "APK 빌드 실패" }
$out = "$root\app\$apkName"
Copy-Item $built $out -Force
Write-Host "== 3) GitHub Release v$Version 생성 =="
gh release create "v$Version" $out --title "JB×AX 맛집 v$Version" --notes $Notes

Write-Host "== 4) 커밋/푸시 =="
Push-Location $root
git add index.html version.json app/android/app/build.gradle app/www/index.html
git commit -m "release: 앱 v$Version - $Notes"
git pull --rebase origin master
git push origin master
Pop-Location
Remove-Item $out -Force
Write-Host "`n✅ 완료. 다운로드: $apkUrl"
Write-Host "   기존 앱은 실행 시 업데이트 배너로 v$Version 안내됨."
