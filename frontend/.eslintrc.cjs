// 2026-07-25 新增（CI 质量门 — 修 P0 短板）
// 之前 4 处 eslint-disable 注释暴露"用过但删了配置"的历史；现在固化。
// 注意：v9 flat config 兼容性问题多，固守 v8 .eslintrc.cjs。
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  settings: { react: { version: "18.3" } },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react/recommended",
    "plugin:react/jsx-runtime",
    "plugin:react-hooks/recommended",
  ],
  plugins: ["@typescript-eslint", "react", "react-hooks", "react-refresh"],
  ignorePatterns: [
    "dist/**",
    "node_modules/**",
    "*.config.js",
    "*.config.cjs",
    "vite.config.d.ts",
  ],
  rules: {
    "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    "react/prop-types": "off",  // TS 强类型已经覆盖
    "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    "@typescript-eslint/no-explicit-any": "warn",
    "@typescript-eslint/ban-ts-comment": "off",  // 项目里允许 // @ts-expect-error for 第三方类型缺失
    "no-empty": ["error", { allowEmptyCatch: true }],
  },
};
