// 악보함 일렉트론 셸 — 파이썬 백엔드(MabiScoreBox.exe 또는 server.py)를 띄우고 그 UI 를 창에 연다.
// 창을 닫으면 백엔드도 같이 끝낸다 (우리가 띄운 경우에만). 데이터는 앱 폴더의 data/ 에 둔다.
const { app, BrowserWindow, shell } = require("electron");
const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const net = require("net");

const PORT = 19997;
const DEV = !app.isPackaged;
// 앱 폴더 = 데이터 위치. 패키지면 악보함.exe 옆, 개발이면 프로젝트 루트
const APP_DIR = DEV ? path.join(__dirname, "..") : path.dirname(process.execPath);
let backend = null;   // 우리가 띄운 백엔드 프로세스
let win = null;

function backendExe() {
  const cands = [
    path.join(process.resourcesPath || "", "MabiScoreBox.exe"),   // 패키지: resources/ 에 동봉
    path.join(APP_DIR, "MabiScoreBox.exe"),                        // 개발: 프로젝트 루트
  ];
  return cands.find((p) => fs.existsSync(p)) || null;
}

function portOpen() {
  return new Promise((resolve) => {
    const s = net.createConnection({ host: "127.0.0.1", port: PORT });
    s.once("connect", () => { s.destroy(); resolve(true); });
    s.once("error", () => resolve(false));
    setTimeout(() => { s.destroy(); resolve(false); }, 400);
  });
}

async function startBackend() {
  if (await portOpen()) { console.log("[scorebox] backend already up"); return; }
  const env = { ...process.env, MABI_NO_BROWSER: "1", MABI_DATA_DIR: APP_DIR, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
  const exe = backendExe();
  if (exe) {
    backend = spawn(exe, [], { cwd: APP_DIR, env, windowsHide: true, stdio: "ignore" });
    console.log("[scorebox] backend exe:", exe);
  } else {
    backend = spawn("python", [path.join(APP_DIR, "server.py")], { cwd: APP_DIR, env, windowsHide: true, stdio: "ignore" });
    console.log("[scorebox] backend: python server.py");
  }
  backend.on("exit", (code) => { console.log("[scorebox] backend exited", code); backend = null; });
  for (let i = 0; i < 60; i++) {           // 최대 12초 대기
    if (await portOpen()) return;
    await new Promise((r) => setTimeout(r, 200));
  }
  console.log("[scorebox] backend did not open port", PORT);
}

function stopBackend() {
  if (!backend) return;
  const pid = backend.pid;
  backend = null;
  // PyInstaller onefile 은 자식 프로세스를 하나 더 만든다 → 트리째 종료
  try { spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {}
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 960, minHeight: 600,
    title: "악보함", backgroundColor: "#101114", autoHideMenuBar: true, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.once("ready-to-show", () => win.show());
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  win.loadURL(`http://127.0.0.1:${PORT}`);
  win.on("closed", () => { win = null; });
}

app.whenReady().then(async () => {
  await startBackend();
  createWindow();
});
app.on("window-all-closed", () => { stopBackend(); app.quit(); });
app.on("before-quit", stopBackend);
