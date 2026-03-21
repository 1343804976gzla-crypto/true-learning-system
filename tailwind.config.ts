import type { Config } from "tailwindcss"
import tailwindcssAnimate from "tailwindcss-animate"

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
    "./templates/**/*.html",
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['"Noto Serif SC"', "Georgia", '"Times New Roman"', "serif"],
        sans: ['-apple-system', '"PingFang SC"', '"Segoe UI"', "Roboto", "sans-serif"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        cream: "#FEFBF6",
        warm: {
          100: "#FAF6F0",
          200: "#F5EDE0",
          300: "#E8DCC8",
          400: "#C4B49A",
          500: "#8B7355",
        },
      },
    },
  },
  plugins: [tailwindcssAnimate],
}

export default config
