/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./gbp/templates/**/*.html",
    "./gbp/static/gbp/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "#6C5DD3",
          50:  "#f4f3fb",
          100: "#e9e7f7",
          200: "#d2ceee",
          300: "#bbb6e6",
          400: "#a59edd",
          500: "#8e85d5",
          600: "#776dce",
          700: "#6C5DD3",
          800: "#5a4dc0",
          900: "#493dad",
        },
        accent: {
          DEFAULT: "#1FB6B6",
          50:  "#f1fafa",
          100: "#e3f5f5",
          200: "#c7ebeb",
          300: "#aae0e0",
          400: "#8ed6d6",
          500: "#72cccc",
          600: "#55c2c2",
          700: "#1FB6B6",
          800: "#17a0a0",
          900: "#108888",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ["Outfit", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      boxShadow: {
        "card": "0 2px 10px -2px rgb(0 0 0 / 0.05), 0 4px 6px -4px rgb(0 0 0 / 0.05)",
        "card-hover": "0 10px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)",
        "glass": "0 8px 32px 0 rgba(0, 0, 0, 0.05)",
      },
      animation: {
        "fade-in": "fadeIn 0.3s ease-out forwards",
        "slide-up": "slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        fadeIn: { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        slideUp: { "0%": { opacity: "0", transform: "translateY(12px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
      },
    },
  },
  plugins: [],
}
