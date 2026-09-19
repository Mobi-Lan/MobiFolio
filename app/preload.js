// 메인 창·오버레이 창에 노출하는 최소 API — 오버레이 창 제어만 (파일·프로세스 접근 없음)
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mobifolio", {
  overlay: {
    state: () => ipcRenderer.invoke("overlay:state"),
    show: (on) => ipcRenderer.invoke("overlay:show", !!on),
    toggle: () => ipcRenderer.invoke("overlay:toggle"),
    clickThrough: (on) => ipcRenderer.invoke("overlay:click", !!on),
    reset: () => ipcRenderer.invoke("overlay:reset"),
    onState: (cb) => { ipcRenderer.on("overlay:state", (_e, s) => cb(s)); },
  },
});
