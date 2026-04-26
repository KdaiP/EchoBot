const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("echobotDesktop", {
  openControlPanel() {
    return ipcRenderer.invoke("desktop:open-control-panel");
  },
  setMousePassthrough(enabled) {
    return ipcRenderer.invoke("desktop:set-mouse-passthrough", enabled);
  },
  getGlobalCursorState() {
    return ipcRenderer.invoke("desktop:get-global-cursor-state");
  },
  startWindowDrag() {
    return ipcRenderer.invoke("desktop:start-window-drag");
  }
});
