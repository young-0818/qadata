import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 开发模式走代理到 qadata serve 默认端口；生产构建由后端同源服务（spec：零 CORS）
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
