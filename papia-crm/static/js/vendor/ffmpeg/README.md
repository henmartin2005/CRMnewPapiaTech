# FFmpeg.wasm browser loader

Vendored upstream UMD files (unmodified), version **0.12.15**:

- https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12.15/dist/umd/ffmpeg.js
- https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12.15/dist/umd/814.ffmpeg.js

Source and MIT license: https://github.com/ffmpegwasm/ffmpeg.wasm

Keep both files together: the loader creates the worker relative to its own URL.
The single-thread core is fetched separately from `@ffmpeg/core@0.12.10` at runtime.
Do not replace it with `core-mt` or add global isolation headers.

The font `static/fonts/Montserrat.ttf` is the static Regular face from
https://github.com/JulietaUla/Montserrat/blob/master/fonts/ttf/Montserrat-Regular.ttf.
Its OFL license is included next to the font.
