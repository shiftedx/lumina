import { defineConfig } from '@playwright/test';
import base from './playwright.realstack.config';

// Focused media journeys use the production fixture server without the unrelated performance seed.
export default defineConfig({
  ...base,
  webServer: Array.isArray(base.webServer) ? [base.webServer[0]] : base.webServer,
  outputDir: '../output/web-native/realstack-results',
});
