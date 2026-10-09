/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./ui/templates/**/*.html",
    "./ui/static/ui/keys.js",
  ],
  // Disable Tailwind's preflight - we handle base styles ourselves
  preflight: false,
}