/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { ink: { 900: "#0b1220", 800: "#111a2e", 700: "#1a2540", 600: "#26345a" } },
    },
  },
  plugins: [],
};
