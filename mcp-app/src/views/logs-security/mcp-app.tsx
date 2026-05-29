import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { baseStyles } from "@shared/theme";

const style = document.createElement("style");
style.textContent = baseStyles;
document.head.appendChild(style);

createRoot(document.getElementById("root")!).render(<App />);
