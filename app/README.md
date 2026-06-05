# JB×AX 맛집 — 네이티브 앱 (Capacitor)

여의나루 점심 맛집 트래커 웹앱을 Android 네이티브 앱으로 감싼 프로젝트.

## 구조 / 동작 방식
- **server.url 방식**: 앱이 라이브 사이트(`https://lunch.jbax.co.kr`)를 WebView로 로드.
  매일 갱신되는 추천이 **앱에서도 항상 최신** → 추천 바뀔 때마다 재빌드 불필요.
- `www/index.html` 은 네트워크 불가 시 폴백 로딩 화면.
- 위치 권한(`ACCESS_FINE/COARSE_LOCATION`) 추가됨 — '내 주변 맛집' GPS용(거부 시 JB빌딩 폴백).
- 설정: `capacitor.config.json` (변경 시 `cd app && npx cap sync android`).

> ⚠️ **SSL 의존성**: `lunch.jbax.co.kr`의 HTTPS 인증서가 발급 완료돼야 앱이 정상 로드돼요.
> (발급 전엔 빈 화면) — 인증서 발급 후 빌드/테스트하세요. 현재 발급 대기 중이면
> `gh api repos/duelspost-droid/jblunch/pages` 의 `https_certificate.state`가 `approved`인지 확인.

## 빌드하려면 (Android)

### 1. Android Studio 설치 (최초 1회)
https://developer.android.com/studio 에서 설치.
설치 시 함께 깔리는 것: JDK, Android SDK, Gradle.

### 2. 환경변수 (보통 Android Studio가 자동 설정)
- `ANDROID_HOME` = SDK 경로 (예: `C:\Users\<사용자>\AppData\Local\Android\Sdk`)
- `JAVA_HOME` = Android Studio 내장 JDK 경로

### 3. 의존성 설치 + 동기화
```bash
cd app
npm install
npx cap sync android
```

### 4. Android Studio에서 열기
```bash
npx cap open android
```
또는 Android Studio에서 `app/android` 폴더를 직접 열기.

### 5. 빌드 / 실행
- **에뮬레이터/실기기 실행**: Android Studio 상단 ▶ Run 버튼
- **APK 생성**: 메뉴 `Build → Build Bundle(s)/APK(s) → Build APK(s)`
  → `app/android/app/build/outputs/apk/debug/app-debug.apk`
- 이 APK를 폰에 설치(사이드로드)하면 바로 사용 가능.

### 6. (선택) Play 스토어 출시
- `Build → Generate Signed Bundle/APK` 로 서명된 `.aab` 생성
- Google Play Console(개발자 등록 $25, 1회)에 업로드

## iOS는?
iOS 빌드는 **macOS + Xcode**가 필요해서 Windows에서는 불가능해요.
Mac이 생기면 `npx cap add ios` 후 동일하게 진행하면 됩니다.

## 설정 변경 후
`capacitor.config.json` 등을 바꾸면 반드시:
```bash
npx cap sync android
```
