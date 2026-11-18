import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": env.DASHBOARD_API_TARGET ?? "http://127.0.0.1:8000",
      },
    },
    test: {
      environment: "jsdom",
    },
  };
});
