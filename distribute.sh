#!/bin/bash
# distribute.sh — Build, sign, notarize, and package FileGenius for distribution
#
# LOCAL usage:
#   ./distribute.sh
#
# CI usage:
#   APPLE_ID=you@example.com APPLE_TEAM_ID=XXXXXXXX APPLE_APP_PASSWORD=xxxx-xxxx \
#   DEVELOPER_ID="Developer ID Application: You (XXXXXXXX)" ./distribute.sh
#
# One-time local setup:
#   xcrun notarytool store-credentials "FileGenius-Notary" \
#     --apple-id "danielalanbates@live.com" \
#     --team-id "MG4YW8XX2Z" \
#     --password "xxxx-xxxx-xxxx-xxxx"

set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="FileGenius"
BUNDLE_ID="com.batesai.filegenius"
DIST_DIR="dist"
BUILD_APP="${DIST_DIR}/${APP_NAME}.app"

VERSION="${GITHUB_REF_NAME:-}"
VERSION="${VERSION#v}"
if [ -z "$VERSION" ]; then
  VERSION=$(plutil -extract CFBundleShortVersionString raw Info.plist 2>/dev/null || echo "4.0.0")
fi

if [ -z "${DEVELOPER_ID:-}" ]; then
  DEVELOPER_ID=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep "Developer ID Application" | head -1 \
    | sed 's/.*) "\(.*\)"/\1/' || true)
fi
if [ -z "${DEVELOPER_ID:-}" ]; then
  echo "❌  No Developer ID cert found."
  exit 1
fi
echo "✅  Signing with: $DEVELOPER_ID"

# ── 1. Compile C launcher ─────────────────────────────────────────────────────
echo "🔨  Compiling launcher (C)…"
clang -O2 -o launcher launcher.c

# ── 2. Assemble .app bundle ───────────────────────────────────────────────────
echo "📦  Assembling .app bundle…"
rm -rf "$BUILD_APP"
mkdir -p "$BUILD_APP/Contents/MacOS"
mkdir -p "$BUILD_APP/Contents/Resources"

# Stamp version into Info.plist
sed "s/<string>4\.0\.0<\/string>/<string>${VERSION}<\/string>/g" Info.plist \
  > "$BUILD_APP/Contents/Info.plist"

cp launcher "$BUILD_APP/Contents/MacOS/FileGenius"
chmod +x "$BUILD_APP/Contents/MacOS/FileGenius"

# Copy Python source into Resources so the launcher can find it
if [ -d src ]; then
  cp -R src/. "$BUILD_APP/Contents/Resources/"
fi

# Icons
for icon in filegenius.icns assets/filegenius.icns; do
  [ -f "$icon" ] && cp "$icon" "$BUILD_APP/Contents/Resources/" && break
done

# ── 3. Entitlements ───────────────────────────────────────────────────────────
ENTITLEMENTS=$(mktemp /tmp/fg_entitlements.XXXXXX.plist)
cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key>                   <true/>
  <key>com.apple.security.cs.disable-library-validation</key>  <true/>
  <key>com.apple.security.automation.apple-events</key>        <true/>
</dict>
</plist>
PLIST

# ── 4. Sign ───────────────────────────────────────────────────────────────────
echo "🔐  Code signing…"
find "$BUILD_APP" -exec xattr -c {} \; 2>/dev/null || true
codesign --force --deep --options runtime \
  --entitlements "$ENTITLEMENTS" \
  --sign "$DEVELOPER_ID" \
  --identifier "$BUNDLE_ID" \
  "$BUILD_APP"
codesign --verify --deep --strict "$BUILD_APP" && echo "✅  Signature valid"
rm -f "$ENTITLEMENTS"

# ── 5. DMG ────────────────────────────────────────────────────────────────────
mkdir -p "$DIST_DIR"
DMG_STAGE=$(mktemp -d)
cp -R "$BUILD_APP" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

DMG="${DIST_DIR}/FileGenius-${VERSION}.dmg"
rm -f "$DMG"
echo "📀  Creating DMG…"
hdiutil create \
  -volname "FileGenius ${VERSION}" \
  -srcfolder "$DMG_STAGE" \
  -ov -format UDZO \
  "$DMG"
rm -rf "$DMG_STAGE"
echo "✅  DMG: $DMG"

# ── 6. Notarize ───────────────────────────────────────────────────────────────
echo "📤  Submitting to Apple Notary Service…"
if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
  xcrun notarytool submit "$DMG" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait --output-format plist | tee notarize_log.json
else
  xcrun notarytool submit "$DMG" \
    --keychain-profile "FileGenius-Notary" \
    --wait --output-format plist | tee notarize_log.json
fi

STATUS=$(plutil -extract "status" raw notarize_log.json 2>/dev/null || echo "unknown")
if [ "$STATUS" != "Accepted" ]; then
  echo "❌  Notarization failed (status: $STATUS)"
  exit 1
fi
echo "✅  Notarization accepted!"

# ── 7. Staple ─────────────────────────────────────────────────────────────────
xcrun stapler staple "$DMG"
spctl --assess --type open --context context:primary-signature "$DMG" \
  && echo "✅  Gatekeeper: passes" \
  || echo "⚠️   Gatekeeper check failed"

echo ""
echo "🎉  $DMG"
afplay /System/Library/Sounds/Glass.aiff 2>/dev/null || true
