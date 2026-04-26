import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const MAIN_JS_PATH = new URL("../desktop/main.js", import.meta.url);

test("desktop main keeps the pet visible across macOS workspaces and fullscreen apps", () => {
    const source = fs.readFileSync(MAIN_JS_PATH, "utf-8");

    assert.match(
        source,
        /mainWindow\.setVisibleOnAllWorkspaces\(true,\s*\{[\s\S]*visibleOnFullScreen:\s*true[\s\S]*\}\);/,
    );
    assert.match(
        source,
        /mainWindow\.setAlwaysOnTop\(true,\s*"screen-saver"\);/,
    );
});
