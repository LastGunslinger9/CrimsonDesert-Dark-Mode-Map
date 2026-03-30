# Dark Mode Map

Dark mode for the map with fully customizable color presets!

## Requirements

- Python 3 — https://www.python.org/downloads/ (check **"Add python.exe to PATH"** during install)

## Install

Double-click **install.bat**. It will:
1. Detect your Crimson Desert game folder via Steam (or prompt you to enter the path manually if not found)
2. Install required Python packages (`lz4`, `cryptography`) if missing
3. Patch the world map CSS in-place

## Uninstall

Double-click **uninstall.bat** to restore vanilla colors.

A backup of the original CSS is saved to the `backup/` folder on first install and is used for restoration.

## After a game update

If the game updates, re-run **install.bat** — the patcher automatically re-reads the game's archive index to find the current file location, so it does not need updating between patches.

## Customization

All map colors are defined in **colors.json**. Edit the `"mod"` values (e.g. `"#030608"`) to create your own color preset and share it with others.
