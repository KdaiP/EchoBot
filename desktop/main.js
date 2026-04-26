const {
  app,
  BrowserWindow,
  Tray,
  Menu,
  ipcMain,
  shell,
  nativeImage,
  screen,
  dialog,
} = require("electron");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const http = require("http");

const HOST = "127.0.0.1";
const PORT = 8000;
const DESKTOP_URL = `http://${HOST}:${PORT}/desktop`;
const WEB_URL = `http://localhost:${PORT}/web`;
const HEALTH_URL = `http://${HOST}:${PORT}/api/health`;
const ROOT_DIR = path.resolve(__dirname, "..");

let mainWindow = null;
let tray = null;
let backendProcess = null;
let backendStopping = false;

function createWindow() {
  const display = screen.getPrimaryDisplay();
  const width = 420;
  const height = 620;

  mainWindow = new BrowserWindow({
    width,
    height,
    x: Math.round(display.workArea.x + display.workArea.width - width - 24),
    y: Math.round(display.workArea.y + display.workArea.height - height - 24),
    frame: false,
    transparent: true,
    resizable: true,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.platform === "darwin") {
    mainWindow.setVisibleOnAllWorkspaces(true, {
      visibleOnFullScreen: true,
    });
    mainWindow.setAlwaysOnTop(true, "screen-saver");
    mainWindow.setFullScreenable(false);
  }

  mainWindow.setIgnoreMouseEvents(true, {
    forward: true,
  });
  mainWindow.loadURL(DESKTOP_URL);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function createTray() {
  const trayIcon = nativeImage.createFromPath(path.join(ROOT_DIR, "echobot", "app", "web", "favicon.svg"));
  tray = new Tray(trayIcon);
  tray.setToolTip("EchoBot Desktop Pet");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: "显示桌宠",
        click() {
          if (!mainWindow) {
            createWindow();
            return;
          }
          mainWindow.show();
          mainWindow.focus();
        },
      },
      {
        label: "打开控制面板",
        click() {
          void shell.openExternal(WEB_URL);
        },
      },
      {
        type: "separator",
      },
      {
        label: "退出",
        click() {
          app.quit();
        },
      },
    ]),
  );
  tray.on("double-click", () => {
    if (!mainWindow) {
      createWindow();
      return;
    }
    if (mainWindow.isVisible()) {
      mainWindow.hide();
      return;
    }
    mainWindow.show();
    mainWindow.focus();
  });
}

function resolvePythonForSpawn() {
  const fromEnv = process.env.ECHOBOT_DESKTOP_PYTHON?.trim();
  if (fromEnv) {
    return { command: fromEnv, argsPrefix: [] };
  }

  const candidates =
    process.platform === "win32"
      ? [["python"], ["python3"], ["py", "-3"]]
      : [["python3"], ["python"]];

  for (const parts of candidates) {
    const [command, ...argsPrefix] = parts;
    const probe = spawnSync(command, [...argsPrefix, "--version"], {
      stdio: "ignore",
      env: process.env,
    });
    if (!probe.error && probe.status === 0) {
      return { command, argsPrefix };
    }
  }

  throw new Error(
    "EchoBot could not find a working Python on PATH. Install Python 3, " +
      "or set ECHOBOT_DESKTOP_PYTHON to the python executable (for example /usr/local/bin/python3).",
  );
}

function spawnBackend() {
  const { command, argsPrefix } = resolvePythonForSpawn();
  backendStopping = false;
  backendProcess = spawn(
    command,
    [...argsPrefix, "-m", "echobot", "app", "--host", HOST, "--port", String(PORT)],
    {
      cwd: ROOT_DIR,
      stdio: "pipe",
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
      },
    },
  );

  backendProcess.stdout.on("data", (chunk) => {
    process.stdout.write(`[echobot] ${chunk}`);
  });
  backendProcess.stderr.on("data", (chunk) => {
    process.stderr.write(`[echobot] ${chunk}`);
  });
  backendProcess.on("exit", (code, signal) => {
    const expected = backendStopping;
    backendProcess = null;
    if (expected) {
      return;
    }
    console.error(`EchoBot backend exited unexpectedly (code=${code}, signal=${signal})`);
  });
}

function stopBackend() {
  if (!backendProcess) {
    return;
  }
  backendStopping = true;
  backendProcess.kill("SIGTERM");
}

function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;

  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(HEALTH_URL, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });

      request.on("error", retry);

      function retry() {
        request.destroy();
        if (Date.now() >= deadline) {
          reject(new Error("EchoBot backend did not become ready in time."));
          return;
        }
        setTimeout(attempt, 500);
      }
    };

    attempt();
  });
}

ipcMain.handle("desktop:open-control-panel", async () => {
  await shell.openExternal(WEB_URL);
});

ipcMain.handle("desktop:start-window-drag", async () => {
  if (!mainWindow) {
    return;
  }
  mainWindow.focus();
});

ipcMain.handle("desktop:set-mouse-passthrough", async (_event, enabled) => {
  if (!mainWindow) {
    return false;
  }

  const passthrough = Boolean(enabled);
  mainWindow.setIgnoreMouseEvents(passthrough, {
    forward: passthrough,
  });
  return passthrough;
});

ipcMain.handle("desktop:get-global-cursor-state", async () => {
  if (!mainWindow) {
    return null;
  }

  const cursorPoint = screen.getCursorScreenPoint();
  const bounds = mainWindow.getBounds();

  return {
    cursorX: cursorPoint.x,
    cursorY: cursorPoint.y,
    windowBounds: {
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
    },
  };
});

app.whenReady().then(async () => {
  try {
    spawnBackend();
    await waitForBackend();
  } catch (error) {
    console.error(error);
    const message = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox("EchoBot Desktop", message);
    app.quit();
    return;
  }

  createWindow();
  createTray();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", (event) => {
  event.preventDefault();
  if (mainWindow) {
    mainWindow.hide();
  }
});

app.on("before-quit", () => {
  stopBackend();
});
