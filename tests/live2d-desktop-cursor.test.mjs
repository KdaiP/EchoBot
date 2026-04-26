import test from "node:test";
import assert from "node:assert/strict";

import {
    hasUsableDesktopCursorBridge,
    mapScreenPointToStagePoint,
} from "../echobot/app/web/features/live2d/desktop-cursor.js";

test("hasUsableDesktopCursorBridge accepts desktop bridge with global cursor reader", () => {
    assert.equal(
        hasUsableDesktopCursorBridge({
            getGlobalCursorState() {},
        }),
        true,
    );
    assert.equal(hasUsableDesktopCursorBridge({}), false);
    assert.equal(hasUsableDesktopCursorBridge(null), false);
});

test("mapScreenPointToStagePoint converts screen coordinates into stage-local coordinates", () => {
    const point = mapScreenPointToStagePoint(
        {
            cursorX: 420,
            cursorY: 260,
        },
        {
            x: 300,
            y: 200,
            width: 420,
            height: 620,
        },
        {
            left: 0,
            top: 0,
            width: 420,
            height: 620,
        },
    );

    assert.deepEqual(point, {
        x: 120,
        y: 60,
    });
});

test("mapScreenPointToStagePoint returns null for incomplete cursor or bounds data", () => {
    assert.equal(mapScreenPointToStagePoint(null, null, null), null);
    assert.equal(
        mapScreenPointToStagePoint(
            {
                cursorX: Number.NaN,
                cursorY: 1,
            },
            {
                x: 0,
                y: 0,
                width: 1,
                height: 1,
            },
            {
                left: 0,
                top: 0,
                width: 1,
                height: 1,
            },
        ),
        null,
    );
});
