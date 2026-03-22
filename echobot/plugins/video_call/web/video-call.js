/**
 * EchoBot 视频通话模块
 * 处理视频采集、WebSocket 通信和 UI 集成
 */

export function createVideoCallModule(deps) {
    const {
        addSystemMessage,
        requestJson,
        responseToError,
        sendMessage,
    } = deps;

    let videoStream = null;
    let videoCanvas = null;
    let videoContext = null;
    let websocket = null;
    let isRecording = false;
    let frameRate = 1; // 每秒采集 1 帧

    function initialize() {
        createVideoControls();
        setupEventListeners();
    }

    function createVideoControls() {
        const controlsDiv = document.getElementById('video-call-controls');
        if (!controlsDiv) {
            const inputArea = document.querySelector('.input-area') || 
                            document.querySelector('.message-input-area') ||
                            document.querySelector('[class*="input"]');
            
            if (inputArea) {
                const newDiv = document.createElement('div');
                newDiv.id = 'video-call-controls';
                newDiv.className = 'video-call-controls';
                newDiv.innerHTML = `
                    <button id="start-video-call" class="btn btn-primary" title="启动视频通话">
                        📹 视频通话
                    </button>
                    <button id="stop-video-call" class="btn btn-danger" disabled style="display:none;" title="停止视频通话">
                        ⏹️ 停止
                    </button>
                    <div id="video-status" class="video-status"></div>
                `;
                inputArea.parentNode.insertBefore(newDiv, inputArea);
            }
        }
    }

    function setupEventListeners() {
        const startBtn = document.getElementById('start-video-call');
        const stopBtn = document.getElementById('stop-video-call');

        if (startBtn) {
            startBtn.addEventListener('click', startVideoCall);
        }
        if (stopBtn) {
            stopBtn.addEventListener('click', stopVideoCall);
        }
    }

    async function startVideoCall() {
        try {
            videoStream = await navigator.mediaDevices.getUserMedia({
                video: { width: 1280, height: 720 },
                audio: false
            });

            videoCanvas = document.createElement('canvas');
            videoCanvas.width = 1280;
            videoCanvas.height = 720;
            videoContext = videoCanvas.getContext('2d');

            const videoElement = document.createElement('video');
            videoElement.srcObject = videoStream;
            videoElement.play();

            await connectWebSocket();

            isRecording = true;
            captureFrames(videoElement);

            updateVideoUI(true);
            addSystemMessage('✓ 视频通话已启动，每秒发送一帧图片');
        } catch (error) {
            addSystemMessage(`✗ 启动视频失败: ${error.message}`);
        }
    }

    function stopVideoCall() {
        isRecording = false;

        if (videoStream) {
            videoStream.getTracks().forEach(track => track.stop());
            videoStream = null;
        }

        if (websocket) {
            websocket.close();
            websocket = null;
        }

        updateVideoUI(false);
        addSystemMessage('✓ 视频通话已停止');
    }

    async function connectWebSocket() {
        return new Promise((resolve, reject) => {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/api/web/video/stream`;

            websocket = new WebSocket(wsUrl);

            websocket.onopen = () => {
                console.log('✓ WebSocket connected');
                resolve();
            };

            websocket.onmessage = (event) => {
                handleVisionContext(JSON.parse(event.data));
            };

            websocket.onerror = (error) => {
                console.error('WebSocket error:', error);
                reject(error);
            };

            websocket.onclose = () => {
                console.log('WebSocket closed');
            };
        });
    }

    function captureFrames(videoElement) {
        const interval = 1000 / frameRate; // 毫秒

        const captureFrame = () => {
            if (!isRecording) return;

            try {
                videoContext.drawImage(videoElement, 0, 0, videoCanvas.width, videoCanvas.height);

                videoCanvas.toBlob((blob) => {
                    if (websocket && websocket.readyState === WebSocket.OPEN) {
                        websocket.send(blob);
                    }
                }, 'image/jpeg', 0.8);
            } catch (error) {
                console.error('Frame capture error:', error);
            }

            setTimeout(captureFrame, interval);
        };

        captureFrame();
    }

    function handleVisionContext(visionContext) {
        const statusDiv = document.getElementById('video-status');
        if (statusDiv) {
            const faceCount = visionContext.faces ? visionContext.faces.length : 0;
            const description = visionContext.image_description || '处理中...';

            statusDiv.innerHTML = `
                <div class="vision-info">
                    <p>📸 ${description.substring(0, 60)}...</p>
                    <p>👤 检测到 ${faceCount} 张人脸</p>
                </div>
            `;
        }

        // 将图像描述插入到消息上下文
        if (visionContext.image_description) {
            insertVisionToContext(visionContext);
        }

        // 如果检测到人脸，显示人脸信息
        if (visionContext.faces && visionContext.faces.length > 0) {
            visionContext.faces.forEach(face => {
                if (face.person_name) {
                    console.log(`✓ 识别到: ${face.person_name} (置信度: ${face.confidence.toFixed(2)})`);
                }
            });
        }
    }

    function insertVisionToContext(visionContext) {
        // 构建视觉信息文本
        const timestamp = new Date(visionContext.timestamp * 1000).toLocaleTimeString();
        const faceInfo = visionContext.faces && visionContext.faces.length > 0
            ? `检测到 ${visionContext.faces.length} 张人脸: ${visionContext.faces.map(f => f.person_name || '未知').join(', ')}`
            : '未检测到人脸';

        const visionText = `[视觉信息 ${timestamp}] ${visionContext.image_description} | ${faceInfo}`;

        // 这里可以将视觉信息存储到全局上下文中
        // 供大模型在生成回复时使用
        if (window.__visionContext) {
            window.__visionContext.push({
                timestamp: visionContext.timestamp,
                description: visionContext.image_description,
                faces: visionContext.faces,
            });
            // 保持最近 80 条
            if (window.__visionContext.length > 80) {
                window.__visionContext.shift();
            }
        } else {
            window.__visionContext = [{
                timestamp: visionContext.timestamp,
                description: visionContext.image_description,
                faces: visionContext.faces,
            }];
        }

        console.log(`✓ 视觉上下文已更新 (总计: ${window.__visionContext.length} 帧)`);
    }

    function updateVideoUI(isActive) {
        const startBtn = document.getElementById('start-video-call');
        const stopBtn = document.getElementById('stop-video-call');

        if (startBtn) {
            startBtn.disabled = isActive;
            startBtn.style.display = isActive ? 'none' : 'block';
        }
        if (stopBtn) {
            stopBtn.disabled = !isActive;
            stopBtn.style.display = isActive ? 'block' : 'none';
        }
    }

    return {
        initialize,
        startVideoCall,
        stopVideoCall,
    };
}

