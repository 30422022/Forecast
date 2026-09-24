import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "GRIDCAST_");
  const target = env.GRIDCAST_API_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/health": target,
        "/v1": target,
        "/docs": target,
        "/openapi.json": target
      }
    },
    build: { outDir: "dist", sourcemap: false }
  };
});
