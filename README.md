# Miller Columns for Nemo

A Nemo extension that brings column-based file browsing directly to your current window, 
featuring instant file previews and a strong focus on speed and usability.

The view runs in-process through GTK 3 and nemo-python.

![Miller columns in Nemo: a chain of folders and a preview of the selected file](preview.png)

## Install

From this repository:

```bash
./install.sh --skip-deps
```

The script symlinks `nemo_miller_columns.py` into
`~/.local/share/nemo-python/extensions` and quits Nemo so the extension
reloads. Open Nemo again after it exits.

Omit `--skip-deps` on a machine that does not yet have Nemo, nemo-python,
or the GTK 3 introspection packages. The script then installs those
packages with apt.

## Uninstall

```bash
./install.sh --uninstall
```

This removes the extension symlink and quits Nemo. Settings already stored
by Nemo stay in place.

## Features

### Turn the view on

Use the toolbar button or press Ctrl+4.

### Search

Type in a column to filter files and folders. Matching ignores case.

![Miller columns in Nemo: a chain of folders and a preview of the selected file](search-preview.png)

### Folder preference

Each folder can remember this view. The extension follows Nemo's
`ignore-view-metadata` and `inherit-folder-viewer` preferences in
`org.nemo.preferences`. When view metadata is ignored, the toolbar choice
applies to the window and is not written onto the folder.

### Language

The interface default language is English.
New languages can be added in [`translations/`](translations/).
