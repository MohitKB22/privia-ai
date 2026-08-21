# Application icons

Tauri expects the following files here before `npm run tauri build` will succeed:

```
32x32.png  128x128.png  128x128@2x.png  icon.icns  icon.ico
```

Generate them from a single 1024x1024 PNG:

```bash
npm run tauri icon path/to/privia-1024.png
```

Icons are intentionally not committed: the repository ships no binary assets, so
every artifact in it is reviewable as text.
