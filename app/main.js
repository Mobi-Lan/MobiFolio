// 모비폴리오(MobiFolio) 일렉트론 셸 — 파이썬 백엔드(MobiFolioCore.exe 또는 server.py)를 띄우고 그 UI 를 창에 연다.
// 배포판: 실행마다 빈 포트를 골라 백엔드를 띄우고, 한 번 쓰는 비밀 토큰을 넘긴다 (다른 프로세스가 포트를 선점해 우리 창에 끼어들지 못하게).
// 창을 닫으면 백엔드에 정상 종료를 요청하고, 안 끝나면 강제 종료한다. 데이터는 %LOCALAPPDATA%\MobiFolio.
const { app, BrowserWindow, shell, dialog, session, Menu, globalShortcut, ipcMain, screen } = require("electron");
const { spawn, spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const net = require("net");
const http = require("http");
const crypto = require("crypto");

const DEV = !app.isPackaged;
const DEV_PORT = 19997;                       // 개발(run.cmd / python server.py)은 고정 포트, 토큰 없음
let PORT = DEV_PORT;
let TOKEN = "";                                // 배포판: 실행마다 새로 만든다
// 앱 폴더: 패키지면 MobiFolio.exe 옆, 개발이면 프로젝트 루트. 데이터·로그는 사용자 폴더 %LOCALAPPDATA%\MobiFolio (백엔드와 같은 규칙)
const APP_DIR = DEV ? path.join(__dirname, "..") : path.dirname(process.execPath);
const DATA_BASE = process.env.MABI_DATA_DIR && DEV ? process.env.MABI_DATA_DIR : (process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "MobiFolio") : APP_DIR);
try { fs.mkdirSync(DATA_BASE, { recursive: true }); } catch {}
const LOG = path.join(DATA_BASE, "electron.log");
let backend = null;   // 우리가 띄운 백엔드 프로세스
let win = null;
let overlay = null;   // 게임 위에 띄우는 투명 오버레이 창 (연주·에린 시간·가공 현황)
let clickThrough = false;
const OVERLAY_FILE = path.join(DATA_BASE, "overlay.json");   // {x,y,width,height,visible}

const origin = () => `http://127.0.0.1:${PORT}`;

function log(...a) {
  const line = `${new Date().toISOString()} ${a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ").replace(/[\r\n]+/g, " ")}\n`;
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

function portOpen(port) {
  return new Promise((resolve) => {
    const s = net.createConnection({ host: "127.0.0.1", port });
    s.once("connect", () => { s.destroy(); resolve(true); });
    s.once("error", () => resolve(false));
    setTimeout(() => { s.destroy(); resolve(false); }, 400);
  });
}

// OS 가 비어 있다고 알려 주는 포트 하나
function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

// GET/POST 를 짧게 — 응답 JSON 또는 null
function req(method, p, timeout = 1500) {
  return new Promise((resolve) => {
    const headers = { "X-Requested-With": "mobifolio", "X-MobiFolio-Token": TOKEN };
    if (method === "POST") { headers["Content-Type"] = "application/json"; headers["Content-Length"] = 2; }
    const r = http.request({ host: "127.0.0.1", port: PORT, path: p, method, timeout, headers }, (res) => {
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
  for (let i = 0; i < tries; i++) { if ((await portOpen(PORT)) === open) return true; await sleep(200); }
  return false;
}

function fatal(title, msg) {
  log("[fatal]", title, msg);
  dialog.showErrorBox(title, `${msg}\n\n로그: ${LOG}`);
  app.exit(1);
}

async function startBackend() {
  if (DEV) {
    // 개발: 이미 떠 있는 백엔드(run.cmd 등)가 우리 것인지 확인하고 붙는다
    if (await portOpen(PORT)) {
      const h = await req("GET", "/api/health");
      if (!h || h.app !== "mobifolio") return fatal("포트 사용 중", `127.0.0.1:${PORT} 를 다른 프로그램이 쓰고 있어 모비폴리오를 열 수 없습니다.`);
      log("[mobifolio] attaching to running backend (dev)", h.pid); return;
    }
  } else {
    // 배포판: 기존 리스너에는 절대 붙지 않는다 — 빈 포트를 새로 고르고 토큰을 만든다
    try { PORT = await freePort(); } catch (e) { return fatal("포트 할당 실패", String(e)); }
    TOKEN = crypto.randomBytes(24).toString("hex");
  }
  const env = { ...process.env, MABI_NO_BROWSER: "1", MABI_LEGACY_DIR: APP_DIR, MABI_PARENT_PID: String(process.pid),
                MABI_PLAYLIST_PORT: String(PORT), MABI_TOKEN: TOKEN, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
  const exe = backendExe();
  const devOut = DEV ? fs.openSync(path.join(DATA_BASE, "server-dev.log"), "a") : null;
  const opts = { cwd: DATA_BASE, env, windowsHide: true, stdio: devOut ? ["ignore", devOut, devOut] : "ignore" };
  try {
    backend = exe ? spawn(exe, [], opts) : spawn("python", [path.join(APP_DIR, "server.py")], opts);
  } catch (e) { return fatal("백엔드 실행 실패", String(e)); }
  log("[mobifolio] backend:", exe || "python server.py", "pid", backend.pid, "port", PORT);
  backend.on("error", (e) => { log("[mobifolio] backend spawn error", String(e)); backend = null; fatal("백엔드 실행 실패", exe ? String(e) : "python 을 찾지 못했습니다. " + String(e)); });
  backend.on("exit", (code) => { log("[mobifolio] backend exited", code); backend = null; });
  if (!(await waitPort(true, 60))) {   // 최대 12초
    return fatal("백엔드 시작 실패", `12초 안에 백엔드가 준비되지 않았습니다.\n${DATA_BASE}\\mobifolio.log 를 확인하세요.`);
  }
  const h = await req("GET", "/api/health");
  if (!h || h.app !== "mobifolio" || (backend && h.pid && backend.pid && h.frozen && h.parent !== process.pid)) {
    return fatal("백엔드 확인 실패", "포트에 응답한 프로세스가 모비폴리오 백엔드가 아닙니다.");
  }
}

let stopping = false;
async function stopBackend() {
  if (!backend || stopping) return;
  stopping = true;
  const pid = backend.pid;
  // 1) 정상 종료 요청 (PyInstaller onefile 이 임시 폴더를 스스로 치우게)  2) 안 끝나면 트리째 강제 종료 (우리가 띄운 PID 만)
  await req("POST", "/api/quit", 800);
  // PyInstaller onefile 은 자식이 끝난 뒤 부모(부트로더)가 %TEMP%\_MEI* 를 지우고 나서야 종료한다 — 그 시간을 충분히 준다 (최대 10초)
  for (let i = 0; i < 100 && backend; i++) await sleep(100);
  if (backend) { log("[mobifolio] backend did not exit in 10s — killing"); try { spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }); } catch {} }
  backend = null;
}

function createWindow() {
  // 페이지가 카메라·마이크·알림 등 권한을 요청해도 전부 거절 (필요 없는 기능)
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  if (!DEV) Menu.setApplicationMenu(null);   // 배포판: 기본 메뉴(개발자 도구·새로고침 단축키) 제거
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 960, minHeight: 600,
    title: "모비폴리오", backgroundColor: "#101114", autoHideMenuBar: true, show: false, icon: path.join(__dirname, "icon", "MobiFolio.ico"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true, devTools: DEV,
                      backgroundThrottling: false,   // 최소화해도 재생 감시(1초 폴링)·곡 전환 타이머가 늦춰지지 않게
                      preload: path.join(__dirname, "preload.js") },   // window.mobifolio.overlay.* (오버레이 창 제어만)
  });
  win.once("ready-to-show", () => win.show());
  const isLocal = (u) => { try { return new URL(u).origin === origin(); } catch { return false; } };
  const isWeb = (u) => /^https?:$/i.test((() => { try { return new URL(u).protocol; } catch { return ""; } })());
  win.webContents.setWindowOpenHandler(({ url }) => { if (isWeb(url)) shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!isLocal(url)) { e.preventDefault(); if (isWeb(url)) shell.openExternal(url); } });
  win.webContents.on("will-redirect", (e, url) => { if (!isLocal(url)) e.preventDefault(); });
  win.webContents.on("did-fail-load", (_e, code, desc) => { log("[mobifolio] did-fail-load", code, desc); if (code !== -3) fatal("화면을 열지 못했습니다", `${desc} (${code})`); });
  win.loadURL(origin() + "/");
  win.on("closed", () => { win = null; if (overlay) { try { overlay.destroy(); } catch {} overlay = null; } });
}

/* ── 오버레이 창: 게임 위에 항상 떠 있는 투명 창. 페이지는 백엔드가 서빙(/overlay.html, 같은 토큰·CSP). ── */
function loadOverlayBounds() {
  try { const o = JSON.parse(fs.readFileSync(OVERLAY_FILE, "utf8")); return o && typeof o === "object" ? o : {}; } catch { return {}; }
}
function saveOverlayBounds(extra = {}) {
  try {
    const b = overlay && !overlay.isDestroyed() ? overlay.getBounds() : {};
    const prev = loadOverlayBounds();
    fs.writeFileSync(OVERLAY_FILE, JSON.stringify({ ...prev, ...b, ...extra }));
  } catch {}
}
function overlayState() {
  return { available: true, visible: !!(overlay && !overlay.isDestroyed() && overlay.isVisible()), clickThrough };
}
function broadcastOverlay() {
  const st = overlayState();
  for (const w of BrowserWindow.getAllWindows()) { try { w.webContents.send("overlay:state", st); } catch {} }
}
function createOverlay() {
  if (overlay && !overlay.isDestroyed()) return overlay;
  const wa = screen.getPrimaryDisplay().workAreaSize;
  const saved = loadOverlayBounds();
  const width = Math.min(Math.max(saved.width || 1000, 360), wa.width), height = Math.min(Math.max(saved.height || 250, 90), wa.height);
  const x = Number.isFinite(saved.x) ? Math.min(Math.max(saved.x, 0), wa.width - 100) : Math.round((wa.width - width) / 2);
  const y = Number.isFinite(saved.y) ? Math.min(Math.max(saved.y, 0), wa.height - 60) : 40;
  overlay = new BrowserWindow({
    width, height, x, y, frame: false, transparent: true, alwaysOnTop: true, resizable: true, skipTaskbar: true, hasShadow: false, show: false,
    minWidth: 300, minHeight: 80, title: "모비폴리오 오버레이", backgroundColor: "#00000000",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true, devTools: DEV, backgroundThrottling: false,
                      preload: path.join(__dirname, "preload.js") },
  });
  overlay.setAlwaysOnTop(true, "screen-saver");   // 전체화면 게임 위에도 뜨게
  overlay.setVisibleOnAllWorkspaces(true);
  overlay.setMenuBarVisibility(false);
  overlay.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  overlay.webContents.on("will-navigate", (e) => e.preventDefault());
  overlay.loadURL(origin() + "/overlay.html");
  overlay.on("moved", () => saveOverlayBounds()); overlay.on("resized", () => saveOverlayBounds());
  overlay.on("closed", () => { overlay = null; broadcastOverlay(); });
  overlay.setIgnoreMouseEvents(clickThrough, { forward: true });
  return overlay;
}
function showOverlay(on) {
  if (on) { createOverlay(); if (!overlay.isVisible()) overlay.showInactive(); }
  else if (overlay && !overlay.isDestroyed()) overlay.hide();
  saveOverlayBounds({ visible: !!on }); broadcastOverlay();
  log("[mobifolio] overlay", on ? "shown" : "hidden");
}
function setClickThrough(on) {
  clickThrough = !!on;
  if (overlay && !overlay.isDestroyed()) overlay.setIgnoreMouseEvents(clickThrough, { forward: true });
  saveOverlayBounds({ clickThrough }); broadcastOverlay();
  log("[mobifolio] overlay click-through", clickThrough ? "on" : "off");
}
function resetOverlay() {
  try { fs.unlinkSync(OVERLAY_FILE); } catch {}
  const wasVisible = overlayState().visible;
  if (overlay && !overlay.isDestroyed()) { overlay.destroy(); overlay = null; }
  clickThrough = false;
  if (wasVisible) showOverlay(true); else broadcastOverlay();
}
ipcMain.handle("overlay:state", () => overlayState());
ipcMain.handle("overlay:show", (_e, on) => { showOverlay(!!on); return overlayState(); });
ipcMain.handle("overlay:toggle", () => { showOverlay(!overlayState().visible); return overlayState(); });
ipcMain.handle("overlay:click", (_e, on) => { setClickThrough(!!on); return overlayState(); });
ipcMain.handle("overlay:reset", () => { resetOverlay(); return overlayState(); });
function registerShortcuts() {
  // F8: 클릭 관통 켜고 끄기 (관통 중엔 창을 잡을 수 없으니 이 키로만 푼다)  F9: 오버레이 보이기/숨기기
  const ok1 = globalShortcut.register("F8", () => setClickThrough(!clickThrough));
  const ok2 = globalShortcut.register("F9", () => showOverlay(!overlayState().visible));
  log("[mobifolio] shortcuts F8", ok1 ? "ok" : "busy", "F9", ok2 ? "ok" : "busy");
}

app.whenReady().then(async () => {
  if (!gotLock) return;
  await startBackend();
  createWindow();
  registerShortcuts();
  const saved = loadOverlayBounds();
  clickThrough = !!saved.clickThrough;
  if (saved.visible) showOverlay(true);   // 지난번에 켜 두었으면 다시 띄운다
});
app.on("will-quit", () => { try { globalShortcut.unregisterAll(); } catch {} });
let quitting = false;
app.on("window-all-closed", () => { app.quit(); });
app.on("before-quit", (e) => {
  if (quitting || !backend) return;
  e.preventDefault(); quitting = true;
  stopBackend().finally(() => app.quit());
});
