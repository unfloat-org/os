import { config } from "@remotion/eslint-config-flat";

export default [
  { ignores: ["fixtures/build/**", "out/**", "node_modules/**"] },
  ...config,
  {
    // Node scripts: the command line, the fixture writer and the tests.
    files: ["bin/**", "fixtures/**", "tests/**"],
    languageOptions: {
      globals: { process: "readonly", console: "readonly" },
    },
  },
];
