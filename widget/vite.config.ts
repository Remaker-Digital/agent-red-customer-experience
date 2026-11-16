/**
 * Agent Red Customer Experience — Widget build configuration.
 *
 * Builds a single self-contained JS file that merchants embed on their
 * storefront. Target: ~15-20KB gzip.
 *
 * Architecture:
 *   - Preact for rendering (~4.5KB gzip)
 *   - Shadow DOM for launcher button (style isolation)
 *   - iframe for conversation panel (full DOM isolation)
 *   - No external CSS files — all styles are inlined via JS
 *
 * © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
 */

/// <reference types="vitest" />
import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
import { resolve } from 'path';
import type { WidgetConfig } from './src/theme/tokens';

const devBackendUrl = process.env.WIDGET_DEV_BACKEND_URL;
const devBackendKey = process.env.WIDGET_DEV_BACKEND_KEY?.trim() ?? '';
const demoWidgetKeyAttribute = 'data-widget-key="widget-dev-fixture"';
const demoWidgetConfig = {
  widget_agent_display_name: 'Agent Red',
  widget_greeting_enabled: true,
  widget_greeting_message: 'Hi there! How can I help you today?',
  widget_sound_enabled: false,
} satisfies WidgetConfig;

export default defineConfig({
  plugins: [
    preact(),
    {
      name: 'widget-dev-config',
      apply: 'serve',
      transformIndexHtml(html, context) {
        if (context.path !== '/dev.html' || !devBackendUrl) return;

        const escapedKey = devBackendKey
          .replaceAll('&', '&amp;')
          .replaceAll('"', '&quot;')
          .replaceAll('<', '&lt;')
          .replaceAll('>', '&gt;');
        return html.replace(demoWidgetKeyAttribute, () => `data-widget-key="${escapedKey}"`);
      },
      configureServer(server) {
        if (devBackendUrl) return;

        server.middlewares.use((request, response, next) => {
          if (request.method !== 'GET' || request.url?.split('?')[0] !== '/api/config') {
            next();
            return;
          }

          response.setHeader('Content-Type', 'application/json');
          response.setHeader('Cache-Control', 'no-store');
          response.end(JSON.stringify({ config: demoWidgetConfig }));
        });
      },
    },
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['tests/**/*.test.{ts,tsx}'],
    globals: true,
  },
  build: {
    lib: {
      entry: resolve(__dirname, 'src/index.ts'),
      name: 'AgentRedWidget',
      fileName: 'agent-red-widget',
      formats: ['iife'],
    },
    rollupOptions: {
      output: {
        // Single file, no code splitting — must be one <script> tag
        inlineDynamicImports: true,
      },
    },
    // Aggressive minification for bundle size
    minify: 'terser',
    terserOptions: {
      compress: {
        // Keep console.warn and console.error for production diagnostics.
        // Only drop console.log and console.debug (verbose/dev-only).
        pure_funcs: ['console.log', 'console.debug'],
        drop_debugger: true,
      },
    },
    // Target modern browsers (same as Shopify storefront requirements)
    target: 'es2020',
    // Report gzip size in build output
    reportCompressedSize: true,
  },
  // Dev server for local testing
  server: {
    port: 3100,
    open: '/dev.html',
    // Proxy /api to backend to avoid CORS during local widget development.
    // The widget sends X-Widget-Key which triggers a CORS preflight that
    // remote backends reject for localhost origins. The proxy makes requests
    // server-to-server, bypassing the browser's same-origin policy.
    // Set WIDGET_DEV_BACKEND_URL to point at staging or local backend.
    proxy: devBackendUrl
      ? {
          '/api': {
            target: devBackendUrl,
            changeOrigin: true,
            secure: true,
          },
        }
      : undefined,
  },
});
