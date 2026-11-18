// Preload: keep Node out of the web page. Add safe bridges here later.
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("openagent", {
  platform: process.platform,
  version: "0.1.0",
});
