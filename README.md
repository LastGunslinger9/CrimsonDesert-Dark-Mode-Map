# Dark Mode Map

Dark mode for the map with fully customizable color presets!

Two types are available. Pick whichever suits you:

---

## JSON Mod Manager

**Requires:** [JSON Mod Manager](https://www.nexusmods.com/crimsondesert/mods/TODO)

---

## Standalone (install.bat)

**Requires:** [Python 3](https://www.python.org/downloads/): Check **"Add python.exe to PATH"** during install.

### Install

Double-click **install.bat**. It will:
1. Detect your Crimson Desert game folder via Steam (or prompt you to enter the path manually if not found)
2. Install required Python packages (`lz4`, `cryptography`) if missing
3. Patch the world map CSS in-place

### Uninstall

Double-click **uninstall.bat** to restore vanilla colors.

A backup of the original CSS is saved to the `backup/` folder on first install and is used for restoration.

### After a game update

Re-run **install.bat**. The patcher automatically re-reads the game's archive index to find the current file location, so it does not need updating between patches.

---

## Customization

All map colors are defined in **colors.json** (standalone version). Edit the `"mod"` values (e.g. `"#030608"`) to create your own color preset and share it with others.

---

## JSON Generator Tool (`JSON-generator-tool/`)

Rebuilds `worldmap_darkmode.json` from the current game files. Useful after a game update invalidates the byte offsets in the JSON mod — run this to regenerate a compatible file without needing a new mod release.

**Requires:** [Python 3](https://www.python.org/downloads/): Check **"Add python.exe to PATH"** during install.

1. Double-click **generate_json.bat**
2. It auto-detects your Steam install (or prompts for the path)
3. Installs required Python packages (`lz4`, `cryptography`) if missing
4. Extracts the current vanilla CSS from the game archives, recalculates all byte offsets, and writes `worldmap_darkmode.json` next to the bat

The output file can then be loaded directly with your JSON mod manager.

> **Note:** This should produce a valid file after most game patches. It will only fail if Pearl Abyss significantly restructures the world map CSS — in which case a new mod release will be needed.

---

## Mod Compatibility Tool (`mod_compatibility_tool/`)

Patches Dark Mode Map colors into the `worldmapview.css` inside **another mod's zip** (e.g. No Fog of War).

**Requires:** [Python 3](https://www.python.org/downloads/): Check **"Add python.exe to PATH"** during install.

1. Drop the target mod `.zip` into the `mod_compatibility_tool/` folder
2. Double-click **patch.bat**
3. If no zip is detected automatically, you'll be prompted to enter the path

Colors are read from **colors.json** in the same folder. Edit them before running to customize.