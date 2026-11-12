import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    lib: {
      entry: 'src/sectors.tsx',
      name: 'SectorExperience',
      formats: ['iife'],
      fileName: () => 'sectors.js',
      cssFileName: 'sectors',
    },
  },
});
