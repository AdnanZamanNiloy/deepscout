import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./theme.css";
import "./layout.css";
import "./components.css";
import "./brand.css";
import { initTheme } from "./theme";
import { installDemoFetch } from "./demo";

/* Static-demo builds run the real console against scripted fixtures: install
 * the fetch interceptor BEFORE the app mounts, so the very first listSessions
 * call is answered locally. No-op (and harmless) in a normal build. */
installDemoFetch();

initTheme();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
