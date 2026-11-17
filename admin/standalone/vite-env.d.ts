// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
