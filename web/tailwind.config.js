/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./dashboard.html",
    "./components/**/*.{js,ts,jsx,tsx}",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./pages/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        forest: {
          DEFAULT: "#2E6F40",
          deep: "#214F2E",
          ink: "#12301D",
          glow: "#8FD3A5",
        },
        "on-forest": {
          DEFAULT: "#FFFFFF",
          muted: "rgba(255, 255, 255, 0.78)",
          faint: "rgba(255, 255, 255, 0.20)",
        },
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
        pixel: ["var(--font-geist-pixel)", "monospace"],
        serif: ["var(--font-geist-serif)", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
}

