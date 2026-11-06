## [latest]

### Added
- `KEYBOARD_PREVIEW_DELAY_MS` in `miller.config` debounces single-file preview after keyboard Up/Down (`bfcfcd92fc32a4e8e642239e69fe683913e3290a`).

### Changed
- Up/Down onto a file waits `KEYBOARD_PREVIEW_DELAY_MS` before the preview is built; clicks, multi-select, Right, and Enter stay immediate (`bfcfcd92fc32a4e8e642239e69fe683913e3290a`).
- Timing and layout knobs live in `miller.config` (`NAVIGATE_DELAY_MS`, `KEYBOARD_DIR_DELAY_MS`, `KEYBOARD_PREVIEW_DELAY_MS`, `MONITOR_DEBOUNCE_MS`, row-build chunk/interval, column and preview sizes, icon sizes). Call sites import those public names; CSS tokens stay in `miller.theme`, view prefs in `miller.preference`. Contract tests lock the values (`58ea83bbc8cfc70f1eae3cccb840b578b24b7afa`, `bfcfcd92fc32a4e8e642239e69fe683913e3290a`).
