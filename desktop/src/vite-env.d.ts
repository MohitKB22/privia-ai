/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PRIVIA_API?: string;
  readonly VITE_PRIVIA_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
