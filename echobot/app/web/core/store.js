export const DEFAULT_SESSION_NAME = "default";
export const DEFAULT_LIP_SYNC_IDS = ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y", "MouthOpenY"];
export const CRON_POLL_INTERVAL_MS = 10000;
export const ASR_STATUS_POLL_INTERVAL_MS = 3000;
export const SESSION_SYNC_POLL_INTERVAL_MS = 4000;
export const TTS_STREAM_FIRST_SEGMENT_SENTENCES = 1;
export const TTS_STREAM_SENTENCE_BATCH_SIZE = 2;
export const TTS_STREAM_MAX_SEGMENT_LENGTH = 140;

export const appState = {
    config: null,
};

export const messageState = {
    counter: 0,
};

export const chatState = {
    chatBusy: false,
    activeChatJobId: "",
    composerImages: [],
    composerFiles: [],
};

export const sessionState = {
    currentSessionName: DEFAULT_SESSION_NAME,
    currentSessionUpdatedAt: "",
    currentSessionHistory: [],
    currentRouteMode: "auto",
    sessions: [],
    sessionLoading: false,
    sessionSyncPollTimerId: 0,
    sessionSyncInFlight: false,
};

export const roleState = {
    currentRoleName: "default",
    currentRoleCard: null,
    roles: [],
    roleLoading: false,
    roleEditorMode: "closed",
};

export const panelState = {
    live2dDrawerOpen: false,
    live2dDrawerTab: "expression",
    roleSidebarOpen: false,
    sessionSidebarOpen: false,
    cronPollTimerId: 0,
    cronLoading: false,
    heartbeatLoaded: false,
    heartbeatLoading: false,
    heartbeatSaving: false,
    heartbeatDirty: false,
    heartbeatData: null,
    heartbeatSavedContent: "",
};

export const runtimeState = {
    delegatedAckEnabled: true,
    shellSafetyMode: "danger-full-access",
    fileWriteEnabled: true,
    cronMutationEnabled: true,
    webPrivateNetworkEnabled: false,
    runtimeConfigLoading: false,
};

export const audioState = {
    selectedTtsProvider: "",
    selectedVoice: "",
    ttsEnabled: true,
    /** When true, web console defers TTS to an active /desktop client. */
    desktopActive: false,
    audioContext: null,
    audioAnalyser: null,
    audioSourceNode: null,
    speaking: false,
    speechEndedResolver: null,
    speechTurnCounter: 0,
    activeSpeechSession: null,
    volumeBuffer: null,
};

export const asrState = {
    asrConfig: null,
    asrProviderUpdating: false,
    microphoneStream: null,
    microphoneSourceNode: null,
    microphoneProcessorNode: null,
    microphoneMuteNode: null,
    microphoneWorkletLoaded: false,
    microphoneChunkResampler: null,
    microphoneCaptureMode: "idle",
    manualRecordingChunks: [],
    asrSocket: null,
    asrSocketIntentionalClose: false,
    asrStatusPollTimerId: 0,
    alwaysListenEnabled: false,
    alwaysListenPaused: false,
    voicePromptQueue: [],
};

export const live2dState = {
    pixiApp: null,
    live2dModel: null,
    live2dStage: null,
    live2dScene: null,
    live2dBackgroundLayer: null,
    live2dParticleLayer: null,
    live2dCharacterLayer: null,
    live2dInternalModel: null,
    live2dDragModel: null,
    live2dDragHandlers: null,
    live2dFocusHandlers: null,
    live2dLoadToken: 0,
    live2dLoading: false,
    live2dPendingSelectionKey: "",
    live2dActiveSelectionKey: "",
    live2dLastPointerX: null,
    live2dLastPointerY: null,
    stageBackgroundSprite: null,
    stageBackgroundLoadToken: 0,
    stagePostFilter: null,
    stageBackgroundBlurFilter: null,
    stageAtmosphereTick: null,
    stageParticleSprites: [],
    stageParticleTextures: null,
    stageResizeObserver: null,
    defaultStageBackgroundTexture: null,
    stageLightCurrentX: 0,
    stageLightCurrentY: 0,
    stageEffects: null,
    currentMouthValue: 0,
    lipSyncFrameId: 0,
    lipSyncHook: null,
    expressionDataCache: new Map(),
    activeExpressionMap: new Map(),
    activeExpressionFiles: [],
    dragging: false,
    dragPointerId: null,
    dragOffsetX: 0,
    dragOffsetY: 0,
    live2dHotkeysEnabled: false,
    live2dMouseFollowEnabled: true,
    selectedStageBackgroundKey: "default",
    currentStageBackgroundTransform: null,
    currentBackgroundImageNaturalSize: null,
};
