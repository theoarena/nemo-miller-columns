# Changelog

## [Latest]

### Improved
- **Inline rename (`F2`):** When renaming a file with an extension, only the filename stem is selected automatically so you don't accidentally overwrite the extension. The text field also reliably retains keyboard focus while typing.
- **Smoother keyboard navigation:** Scrolling rapidly through files with the Up/Down arrow keys briefly pauses preview generation until navigation settles, making keyboard browsing significantly faster and more responsive.

---

## [1.0.0] - 2026-11-06

### Added
- **Miller Columns view:** Browse folders in horizontal cascading columns (macOS Finder style).
- **Toolbar button and shortcut:** Toggle column view quickly with the new Nemo toolbar button or using <kbd>Ctrl</kbd> + <kbd>4</kbd>.
- **Interactive preview pane:** View file details and previews side-by-side. Selecting multiple items shows a summary with item counts and total file size.
- **Type-to-filter search:** Type anywhere inside an active column to filter files and folders by name in real time. Press <kbd>Esc</kbd> to clear the search filter.
- **Multi-selection:** Select multiple files or folders using <kbd>Ctrl</kbd> + Click, <kbd>Shift</kbd> + Arrow keys, or <kbd>Ctrl</kbd> + <kbd>A</kbd>.
- **Drag and drop:** Drag files across columns or to other folders. Hold <kbd>Ctrl</kbd> to copy, <kbd>Shift</kbd> to move, <kbd>Ctrl</kbd> + <kbd>Shift</kbd> to link, or hold <kbd>Alt</kbd> to open an action menu on drop.
- **Native context menu:** Full right-click context menu integration with Nemo, including "Open with...", scripts, file properties, and creating new folders.
- **Localization:** System-wide localization support, including complete Portuguese (pt_BR) translations.

### Improved
- **Full keyboard navigation:** Seamless arrow key support (<kbd>←</kbd> to return to parent, <kbd>→</kbd> or <kbd>Enter</kbd> to open folder, <kbd>↑</kbd>/<kbd>↓</kbd> to move between items).
- **Tab state preservation:** Switching tabs in Nemo preserves the active column, folder path, and selection in each tab.
- **Address bar synchronization:** The Nemo breadcrumb path bar updates automatically to reflect your current location as you navigate between columns.

### Fixed
- Fixed keyboard focus loss when switching between Nemo tabs or returning to Miller view.
- Fixed a brief flash of Nemo's default view when switching to column view.
- Fixed clipboard shortcuts (<kbd>Ctrl+C</kbd>, <kbd>Ctrl+X</kbd>, <kbd>Ctrl+V</kbd>) to consistently operate on the focused column's selection.
