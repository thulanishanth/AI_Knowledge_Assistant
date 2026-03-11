import globals from "globals";
import pluginJs from "@eslint/js";

export default [
  {
    // Apply these rules to all JavaScript files
    files: ["**/*.js"],
    languageOptions: { 
      // Tells ESLint this code runs in a browser (allows window, document, fetch)
      globals: globals.browser 
    }
  },
  // Use industry-standard baseline rules
  pluginJs.configs.recommended,
  {
    // Custom overrides
    rules: {
      "no-unused-vars": "warn",
      "no-console": "off" // Allows you to use console.log() for debugging
    }
  }
];