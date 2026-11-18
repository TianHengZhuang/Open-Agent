// OpenAgent desktop shell.
// Loads the local web UI (docker compose `web` service). If the server is
// not up yet, shows a friendly waiting page and auto-retries.
const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
const path = require("node:path");

const WEB_URL = process.env.OPENAGENT_WEB_URL || "http://localhost:3000";
const API_URL =
  process.env.OPENAGENT_API_URL || "http://localhost:8000/api/v1/health";

let win = null;

function waitingPage(reason) {
  const safeReason = String(reason || "starting").replace(/</g, "&lt;");
  return `data:text/html;charset=utf-8,${encodeURIComponent(`
    <body style="font-family:Segoe UI,Arial;background:#0b0f16;color:#e6edf3;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
      <div style="max-width:520px;text-align:center">
        <h1>OpenAgent starting…</h1>
        <p>${safeReason}</p>
        <p>1) Run: <code>docker compose up -d --build</code><br/>2) Wait for web on ${WEB_URL}</p>
        <p style="opacity:.7">Auto-retrying every 3s…</p>
      </div>
    </body>`)}`;
}

async function serverUp(url) {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(url, { signal: ctrl.signal });
    clearTimeout(t);
    return res.ok;
  } catch {
    return false;
  }
}

async function loadApp() {
  if (!win) return;
  const ok = await serverUp(WEB_URL);
  if (ok) {
    await win.loadURL(WEB_URL);
  } else {
    await win.loadURL(waitingPage(`Waiting for ${WEB_URL}`));
    setTimeout(loadApp, 3000);
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "OpenAgent",
    autoHideMenuBar: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  const menu = Menu.buildFromTemplate([
    {
      label: "OpenAgent",
      submenu: [
        { label: "Reload", accelerator: "CmdOrCtrl+R", click: () => loadApp() },
        {
          label: "Open in browser",
          click: () => shell.openExternal(WEB_URL),
        },
        {
          label: "Check API health",
          click: () => shell.openExternal(API_URL),
        },
        { type: "separator" },
        {
          label: "Toggle DevTools",
          accelerator: "F12",
          click: () => win.webContents.toggleDevTools(),
        },
        { type: "separator" },
        { label: "Quit", accelerator: "CmdOrCtrl+Q", click: () => app.quit() },
      ],
    },
  ]);
  Menu.setApplicationMenu(menu);

  win.on("closed", () => {
    win = null;
  });

  loadApp().catch((err) => {
    dialog.showErrorBox("OpenAgent failed to start", String(err));
  });
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
