import test from "node:test";
import assert from "node:assert/strict";

import {
    createLive2DBroadcastReceiver,
    createLive2DBroadcastSender,
} from "../echobot/app/web/features/live2d/broadcast.js";

class FakeBroadcastChannel {
    static channels = new Map();

    constructor(name) {
        this.name = String(name || "");
        this.listeners = new Set();
        this.closed = false;
        const peers = FakeBroadcastChannel.channels.get(this.name) || new Set();
        peers.add(this);
        FakeBroadcastChannel.channels.set(this.name, peers);
    }

    addEventListener(type, listener) {
        if (type !== "message" || typeof listener !== "function" || this.closed) {
            return;
        }

        this.listeners.add(listener);
    }

    removeEventListener(type, listener) {
        if (type !== "message" || typeof listener !== "function") {
            return;
        }

        this.listeners.delete(listener);
    }

    postMessage(data) {
        if (this.closed) {
            return;
        }

        const peers = FakeBroadcastChannel.channels.get(this.name) || new Set();
        for (const peer of peers) {
            if (peer.closed || peer === this) {
                continue;
            }

            for (const listener of peer.listeners) {
                listener({
                    data,
                });
            }
        }
    }

    close() {
        if (this.closed) {
            return;
        }

        this.closed = true;
        this.listeners.clear();
        const peers = FakeBroadcastChannel.channels.get(this.name);
        if (!peers) {
            return;
        }
        peers.delete(this);
        if (peers.size === 0) {
            FakeBroadcastChannel.channels.delete(this.name);
        }
    }
}

test("sender gracefully degrades when BroadcastChannel is unavailable", () => {
    const originalBroadcastChannel = globalThis.BroadcastChannel;
    delete globalThis.BroadcastChannel;

    try {
        const sender = createLive2DBroadcastSender();
        assert.equal(sender.isAvailable(), false);
        assert.equal(sender.sendModelChanged({ available: true }), false);
        sender.close();
    } finally {
        globalThis.BroadcastChannel = originalBroadcastChannel;
    }
});

test("receiver dispatches all supported live2d message types", () => {
    const originalBroadcastChannel = globalThis.BroadcastChannel;
    globalThis.BroadcastChannel = FakeBroadcastChannel;

    const calls = [];

    try {
        const sender = createLive2DBroadcastSender({
            sourceId: "web",
        });
        const receiver = createLive2DBroadcastReceiver({
            onModelChanged(payload) {
                calls.push(["model", payload.live2dConfig?.selection_key]);
            },
            onExpressionToggled(payload) {
                calls.push(["expression", payload.expressionItem?.file, payload.active]);
            },
            onMotionPlayed(payload) {
                calls.push(["motion", payload.motionItem?.file]);
            },
            onMouthValue(payload) {
                calls.push(["mouth", payload.value]);
            },
            onMouseFollowChanged(payload) {
                calls.push(["follow", payload.enabled]);
            },
        });

        sender.sendModelChanged({
            selection_key: "model-a",
        });
        sender.sendExpressionToggled({
            expressionItem: {
                file: "smile.exp3.json",
            },
            active: true,
        });
        sender.sendMotionPlayed({
            motionItem: {
                file: "wave.motion3.json",
            },
        });
        sender.sendMouthValue(0.42);
        sender.sendMouseFollowChanged(true);

        assert.deepEqual(calls, [
            ["model", "model-a"],
            ["expression", "smile.exp3.json", true],
            ["motion", "wave.motion3.json"],
            ["mouth", 0.42],
            ["follow", true],
        ]);

        receiver.close();
        sender.close();
    } finally {
        globalThis.BroadcastChannel = originalBroadcastChannel;
        FakeBroadcastChannel.channels.clear();
    }
});

test("receiver ignores messages from configured sourceId", () => {
    const originalBroadcastChannel = globalThis.BroadcastChannel;
    globalThis.BroadcastChannel = FakeBroadcastChannel;
    let called = false;

    try {
        const sender = createLive2DBroadcastSender({
            sourceId: "web-self",
        });
        const receiver = createLive2DBroadcastReceiver(
            {
                onModelChanged() {
                    called = true;
                },
            },
            {
                ignoreSourceId: "web-self",
            },
        );

        sender.sendModelChanged({
            selection_key: "model-a",
        });

        assert.equal(called, false);

        receiver.close();
        sender.close();
    } finally {
        globalThis.BroadcastChannel = originalBroadcastChannel;
        FakeBroadcastChannel.channels.clear();
    }
});
