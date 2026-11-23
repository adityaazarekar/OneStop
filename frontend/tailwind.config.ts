import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "#0a0a0f",
        surface: "#12121a",
        "surface-card": "#181824",
        "surface-border": "#272738",
        brand: {
          primary: "#6366f1",
          secondary: "#8b5cf6",
          accent: "#10b981",
        },
      },
    },
  },
  plugins: [],
};
export default config;
