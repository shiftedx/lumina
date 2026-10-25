import { configDefaults, defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
  },
  build: {
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        offline: resolve(__dirname, 'music-offline.html'),
      },
      output: {
        // The ui/ and palette budgets are measured on their own chunks.
        // The palette is the lazy CommandPalette entry chunk, named here rather than via manualChunks: a manual chunk
        // drags every module it imports (api, gallery, luminaModel) into itself and the main bundle then loads it eagerly.
        chunkFileNames: (chunk) => (chunk.facadeModuleId?.includes('/src/features/palette/CommandPalette') ? 'assets/palette-[hash].js' : 'assets/[name]-[hash].js'),
        manualChunks(id) {
          // React and lucide are shared by every chunk; without their own chunk Rollup puts them in `ui`
          // (main.tsx imports ui first) and the ui budget would measure React instead of the primitives.
          if (/\/node_modules\/(react|react-dom|scheduler|lucide-react)\//.test(id)) return 'vendor';
          // The application-wide compatibility surface is shared by every route. Keep it distinct from
          // primitive implementation cost, while the SVG geometry remains a separately cached asset.
          if (id.includes('/src/luminaIcons.tsx') || id.includes('/src/ui/lumina-media-sprite.svg')) return 'icons';
          if (id.includes('/src/ui/')) return 'ui';
          return undefined;
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Setup's cleanup unmounts before each file's afterEach resets mocks/timers.
    sequence: { hooks: 'list' },
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
});
