import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        environment: 'jsdom',
        globals: false,
        include: ['static/js/**/*.test.js'],
        exclude: ['**/node_modules/**'],
    },
});
