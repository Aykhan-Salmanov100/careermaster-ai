// CareerMaster AI — runtime configuration.
//
// The app runs fully in the browser by default. If you deploy the optional
// FastAPI backend (see backend/ and the README), put its public URL here and
// the app will automatically upgrade to real LLM-powered questions, answer
// evaluation, and live suggestions. Leave it empty to stay 100% client-side.
//
// Example:  BACKEND_URL: "https://careermaster-ai.fly.dev"
window.CONFIG = {
  BACKEND_URL: "",

  ENDPOINTS: {
    HEALTH:   "/health",
    PARSE:    "/parse",
    GENERATE: "/interview/generate",
    EVALUATE: "/interview/evaluate",
    SUGGEST:  "/live/suggest",
  },
};
