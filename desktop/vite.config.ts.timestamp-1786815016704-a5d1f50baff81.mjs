// vite.config.ts
import { defineConfig } from "file:///sessions/upbeat-sleepy-lamport/mnt/projects/privia/apps/desktop/node_modules/vite/dist/node/index.js";
import react from "file:///sessions/upbeat-sleepy-lamport/mnt/projects/privia/apps/desktop/node_modules/@vitejs/plugin-react/dist/index.js";
import path from "node:path";
var __vite_injected_original_dirname = "/sessions/upbeat-sleepy-lamport/mnt/projects/privia/apps/desktop";
var API_TARGET = process.env.PRIVIA_API_URL ?? "http://127.0.0.1:8756";
var vite_config_default = defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__vite_injected_original_dirname, "./src") }
  },
  server: {
    port: 5173,
    strictPort: true,
    // Bind to loopback only. The desktop client is not a web app and must not
    // be reachable from the local network.
    host: "127.0.0.1",
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: false },
      "/health": { target: API_TARGET, changeOrigin: false }
    }
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
    chunkSizeWarningLimit: 900
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"]
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcudHMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCIvc2Vzc2lvbnMvdXBiZWF0LXNsZWVweS1sYW1wb3J0L21udC9wcm9qZWN0cy9wcml2aWEvYXBwcy9kZXNrdG9wXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ZpbGVuYW1lID0gXCIvc2Vzc2lvbnMvdXBiZWF0LXNsZWVweS1sYW1wb3J0L21udC9wcm9qZWN0cy9wcml2aWEvYXBwcy9kZXNrdG9wL3ZpdGUuY29uZmlnLnRzXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ltcG9ydF9tZXRhX3VybCA9IFwiZmlsZTovLy9zZXNzaW9ucy91cGJlYXQtc2xlZXB5LWxhbXBvcnQvbW50L3Byb2plY3RzL3ByaXZpYS9hcHBzL2Rlc2t0b3Avdml0ZS5jb25maWcudHNcIjtpbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tICd2aXRlJztcbmltcG9ydCByZWFjdCBmcm9tICdAdml0ZWpzL3BsdWdpbi1yZWFjdCc7XG5pbXBvcnQgcGF0aCBmcm9tICdub2RlOnBhdGgnO1xuXG5jb25zdCBBUElfVEFSR0VUID0gcHJvY2Vzcy5lbnYuUFJJVklBX0FQSV9VUkwgPz8gJ2h0dHA6Ly8xMjcuMC4wLjE6ODc1Nic7XG5cbmV4cG9ydCBkZWZhdWx0IGRlZmluZUNvbmZpZyh7XG4gIHBsdWdpbnM6IFtyZWFjdCgpXSxcbiAgcmVzb2x2ZToge1xuICAgIGFsaWFzOiB7ICdAJzogcGF0aC5yZXNvbHZlKF9fZGlybmFtZSwgJy4vc3JjJykgfSxcbiAgfSxcbiAgc2VydmVyOiB7XG4gICAgcG9ydDogNTE3MyxcbiAgICBzdHJpY3RQb3J0OiB0cnVlLFxuICAgIC8vIEJpbmQgdG8gbG9vcGJhY2sgb25seS4gVGhlIGRlc2t0b3AgY2xpZW50IGlzIG5vdCBhIHdlYiBhcHAgYW5kIG11c3Qgbm90XG4gICAgLy8gYmUgcmVhY2hhYmxlIGZyb20gdGhlIGxvY2FsIG5ldHdvcmsuXG4gICAgaG9zdDogJzEyNy4wLjAuMScsXG4gICAgcHJveHk6IHtcbiAgICAgICcvYXBpJzogeyB0YXJnZXQ6IEFQSV9UQVJHRVQsIGNoYW5nZU9yaWdpbjogZmFsc2UgfSxcbiAgICAgICcvaGVhbHRoJzogeyB0YXJnZXQ6IEFQSV9UQVJHRVQsIGNoYW5nZU9yaWdpbjogZmFsc2UgfSxcbiAgICB9LFxuICB9LFxuICBidWlsZDoge1xuICAgIG91dERpcjogJ2Rpc3QnLFxuICAgIHNvdXJjZW1hcDogZmFsc2UsXG4gICAgdGFyZ2V0OiAnZXMyMDIyJyxcbiAgICBjaHVua1NpemVXYXJuaW5nTGltaXQ6IDkwMCxcbiAgfSxcbiAgdGVzdDoge1xuICAgIGVudmlyb25tZW50OiAnanNkb20nLFxuICAgIGdsb2JhbHM6IHRydWUsXG4gICAgc2V0dXBGaWxlczogWycuL3NyYy90ZXN0L3NldHVwLnRzJ10sXG4gICAgaW5jbHVkZTogWydzcmMvKiovKi50ZXN0Lnt0cyx0c3h9J10sXG4gIH0sXG59KTtcbiJdLAogICJtYXBwaW5ncyI6ICI7QUFBa1gsU0FBUyxvQkFBb0I7QUFDL1ksT0FBTyxXQUFXO0FBQ2xCLE9BQU8sVUFBVTtBQUZqQixJQUFNLG1DQUFtQztBQUl6QyxJQUFNLGFBQWEsUUFBUSxJQUFJLGtCQUFrQjtBQUVqRCxJQUFPLHNCQUFRLGFBQWE7QUFBQSxFQUMxQixTQUFTLENBQUMsTUFBTSxDQUFDO0FBQUEsRUFDakIsU0FBUztBQUFBLElBQ1AsT0FBTyxFQUFFLEtBQUssS0FBSyxRQUFRLGtDQUFXLE9BQU8sRUFBRTtBQUFBLEVBQ2pEO0FBQUEsRUFDQSxRQUFRO0FBQUEsSUFDTixNQUFNO0FBQUEsSUFDTixZQUFZO0FBQUE7QUFBQTtBQUFBLElBR1osTUFBTTtBQUFBLElBQ04sT0FBTztBQUFBLE1BQ0wsUUFBUSxFQUFFLFFBQVEsWUFBWSxjQUFjLE1BQU07QUFBQSxNQUNsRCxXQUFXLEVBQUUsUUFBUSxZQUFZLGNBQWMsTUFBTTtBQUFBLElBQ3ZEO0FBQUEsRUFDRjtBQUFBLEVBQ0EsT0FBTztBQUFBLElBQ0wsUUFBUTtBQUFBLElBQ1IsV0FBVztBQUFBLElBQ1gsUUFBUTtBQUFBLElBQ1IsdUJBQXVCO0FBQUEsRUFDekI7QUFBQSxFQUNBLE1BQU07QUFBQSxJQUNKLGFBQWE7QUFBQSxJQUNiLFNBQVM7QUFBQSxJQUNULFlBQVksQ0FBQyxxQkFBcUI7QUFBQSxJQUNsQyxTQUFTLENBQUMsd0JBQXdCO0FBQUEsRUFDcEM7QUFDRixDQUFDOyIsCiAgIm5hbWVzIjogW10KfQo=
