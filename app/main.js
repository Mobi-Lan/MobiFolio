// 모비폴리오(MobiFolio) 일렉트론 셸 — 파이썬 백엔드(MobiFolioCore.exe 또는 server.py)를 띄우고 그 UI 를 창에 연다.
// 창을 닫으면 백엔드도 같이 끝낸다 (정상 종료 요청 → 안 되면 강제). 데이터는 앱 폴더의 data/ 에 둔다.
const { app, BrowserWindow, shell, dialog } = require("electron");
const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const net = require("net");
const http = require("http");

const PORT = 19997;
const DEV = !app.isPackaged;
// 앱 폴더: 패키지면 MobiFolio.exe 옆, 개발이면 프로젝트 루트. 데이터·로그는 사용자 폴더 %LOCALAPPDATA%\MobiFolio (백엔드와 같은 규칙)
const APP_DIR = DEV ? path.join(__dirname, "..") : path.dirname(process.execPath);
const DATA_BASE = process.env.MABI_DATA_DIR || (process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "MobiFolio") : APP_DIR);
try { fs.mkdirSync(DATA_BASE, { recursive: true }); } catch {}
const LOG = path.join(DATA_BASE, "electron.log");
let backend = null;   // 우리가 띄운 백엔드 프로세스
let win = null;

function log(...a) {
  const line = `${new Date().toISOString()} ${a.join(" ")}\n`;
  try { fs.appendFileSync(LOG, line); } catch {}
  if (DEV) console.log(line.trim());
}

// 한 번에 하나만: 두 번째 실행은 기존 창을 앞으로
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) { app.quit(); }
app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

function backendExe() {
  const cands = [
    path.join(process.resourcesPath || "", "MobiFolioCore.exe"),   // 패키지: resources/ 에 동봉
    path.join(APP_DIR, "MobiFolioCore.exe"),                        // 개발: 프로젝트 루트
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

// GET/POST 를 짧게 — 응답 JSON 또는 null
function req(method, p, timeout = 1500) {
  return new Promise((resolve) => {
    const r = http.request({ host: "127.0.0.1", port: PORT, path: p, method, timeout,
      headers: method === "POST" ? { "Content-Type": "application/json", "Content-Length": 2, "X-Requested-With": "mobifolio" } : { "X-Requested-With": "mobifolio" } }, (res) => {
      const bufs = []; res.on("data", (b) => bufs.push(b));
      res.on("end", () => { try { resolve(JSON.parse(Buffer.concat(bufs).toString("utf8"))); } catch { resolve(null); } });
    });
    r.on("error", () => resolve(null)); r.on("timeout", () => { r.destroy(); resolve(null); });
    if (method === "POST") r.write("{}");
    r.end();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitPort(open, tries = 60) {   // 200ms × tries
  for (let i = 0; i < tries; i++) { if ((await portOpen()) === open) return true; await sleep(200); }
  return false;
}

function fatal(title, msg) {
  log("[fatal]", title, msg);
  dialog.showErrorBox(title, `${msg}\n\n로그: ${LOG}`);
  app.exit(1);
}

async function startBackend() {
  if (await portOpen()) {
    // 누가 포트를 쓰고 있나? 모비폴리오 백엔드가 아니면 못 붙는다
    const h = await req("GET", "/api/health");
    if (!h || h.app !== "mobifolio") return fatal("포트 사용 중", `127.0.0.1:${PORT} 를 다른 프로그램이 쓰고 있어 모비폴리오를 열 수 없습니다.`);
    if (DEV) { log("[mobifolio] attaching to running backend (dev)", h.pid); return; }
    // 이전 실행이 남긴 백엔드 → 정상 종료시키고 새로 띄운다 (빌드·데이터 위치가 다를 수 있으므로)
    log("[mobifolio] stale backend found, asking it to quit", h.pid, h.data_dir);
    await req("POST", "/api/quit");
    if (!(await waitPort(false, 25))) {
      try { spawnSync("taskkill", ["/PID", String(h.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {}
      await waitPort(false, 10);
    }
  }
  // MABI_LEGACY_DIR: 예전 빌드가 앱 폴더 data/ 에 두던 데이터를 백엔드가 처음 한 번 사용자 폴더로 옮길 수 있게
  const env = { ...process.env, MABI_NO_BROWSER: "1", MABI_LEGACY_DIR: APP_DIR, MABI_PARENT_PID: String(process.pid), PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
  const exe = backendExe();
  const devOut = DEV ? fs.openSync(path.join(DATA_BASE, "server-dev.log"), "a") : null;
  const opts = { cwd: APP_DIR, env, windowsHide: true, stdio: devOut ? ["ignore", devOut, devOut] : "ignore" };
  try {
    backend = exe ? spawn(exe, [], opts) : spawn("python", [path.join(APP_DIR, "server.py")], opts);
  } catch (e) { return fatal("백엔드 실행 실패", String(e)); }
  log("[mobifolio] backend:", exe || "python server.py", "pid", backend.pid);
  backend.on("error", (e) => { log("[mobifolio] backend spawn error", e); backend = null; fatal("백엔드 실행 실패", exe ? String(e) : "python 을 찾지 못했습니다. " + String(e)); });
  backend.on("exit", (code) => { log("[mobifolio] backend exited", code); backend = null; });
  if (!(await waitPort(true, 60))) {   // 최대 12초
    return fatal("백엔드 시작 실패", `12초 안에 백엔드가 준비되지 않았습니다.\n${DATA_BASE}\\mobifolio.log 를 확인하세요.`);
  }
}

let stopping = false;
async function stopBackend() {
  if (!backend || stopping) return;
  stopping = true;
  const pid = backend.pid;
  // 1) 정상 종료 요청 (PyInstaller onefile 이 임시 폴더를 스스로 치우게)  2) 안 끝나면 트리째 강제 종료
  await req("POST", "/api/quit", 800);
  for (let i = 0; i < 15 && backend; i++) await sleep(100);
  if (backend) { try { spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {} }
  backend = null;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 960, minHeight: 600,
    title: "모비폴리오", backgroundColor: "#101114", autoHideMenuBar: true, show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.once("ready-to-show", () => win.show());
  const isLocal = (u) => u.startsWith(`http://127.0.0.1:${PORT}`);
  win.webContents.setWindowOpenHandler(({ url }) => { if (/^https?:/i.test(url)) shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!isLocal(url)) { e.preventDefault(); if (/^https?:/i.test(url)) shell.openExternal(url); } });
  win.webContents.on("did-fail-load", (_e, code, desc) => { log("[mobifolio] did-fail-load", code, desc); if (code !== -3) fatal("화면을 열지 못했습니다", `${desc} (${code})`); });
  win.loadURL(`http://127.0.0.1:${PORT}`);
  win.on("closed", () => { win = null; });
}

app.whenReady().then(async () => {
  if (!gotLock) return;
  await startBackend();
  createWindow();
});
let quitting = false;
app.on("window-all-closed", () => { app.quit(); });
app.on("before-quit", (e) => {
  if (quitting || !backend) return;
  e.preventDefault(); quitting = true;
  stopBackend().finally(() => app.quit());
});
