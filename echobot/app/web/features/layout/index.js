import { createCronController } from "./cron.js";
import { createHeartbeatController } from "./heartbeat.js";
import { createPanelController } from "./panels.js";
import { createRuntimeController } from "./runtime.js";
import { createSidebarController } from "./sidebars.js";
import { createSplitController } from "./split.js";

export function createLayoutModule(deps) {
    let cron;
    let heartbeat;

    const panels = createPanelController({
        handleCronPanelToggle() {
            cron.handleCronPanelToggle();
        },
        handleHeartbeatPanelToggle() {
            heartbeat.handleHeartbeatPanelToggle();
        },
    });
    const sidebars = createSidebarController();
    const split = createSplitController();
    const runtime = createRuntimeController(deps);
    cron = createCronController({
        formatTimestamp: deps.formatTimestamp,
        isSettingsPanelOpen: panels.isSettingsPanelOpen,
        requestJson: deps.requestJson,
        setRunStatus: deps.setRunStatus,
    });
    heartbeat = createHeartbeatController({
        isSettingsPanelOpen: panels.isSettingsPanelOpen,
        requestJson: deps.requestJson,
    });

    return {
        ...cron,
        ...heartbeat,
        ...panels,
        ...runtime,
        ...sidebars,
        ...split,
    };
}
