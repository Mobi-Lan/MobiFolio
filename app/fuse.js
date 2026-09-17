// 패키징 뒤 Electron 퓨즈를 잠근다 — MobiFolio.exe 가 Node 실행기(ELECTRON_RUN_AS_NODE / NODE_OPTIONS / --inspect)로
// 쓰이거나 resources\app 폴더로 asar 를 대체당하지 않게. 사용: node fuse.js <MobiFolio.exe 경로>
const path = require("path");
const fs = require("fs");
const { flipFuses, FuseVersion, FuseV1Options, getCurrentFuseWire } = require("@electron/fuses");

const exe = process.argv[2] || path.join(__dirname, "..", "dist-electron", "MobiFolio-win32-x64", "MobiFolio.exe");
if (!fs.existsSync(exe)) { console.error("exe not found:", exe); process.exit(1); }

(async () => {
  await flipFuses(exe, {
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: false,   // packager 가 무결성 리소스를 쓰지 않으면 켜면 실행이 안 된다
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
  });
  const wire = await getCurrentFuseWire(exe);
  const on = (k) => { const v = String(wire[k]); return v === "1" || v === "49" ? "on" : v === "0" || v === "48" ? "off" : "?"; };
  console.log(`fuses: RunAsNode=${on(FuseV1Options.RunAsNode)} NODE_OPTIONS=${on(FuseV1Options.EnableNodeOptionsEnvironmentVariable)} inspect=${on(FuseV1Options.EnableNodeCliInspectArguments)} OnlyLoadAppFromAsar=${on(FuseV1Options.OnlyLoadAppFromAsar)}`);
})().catch((e) => { console.error(e); process.exit(1); });
